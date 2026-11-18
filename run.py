import argparse
import json
from pathlib import Path
import posixpath
import subprocess
import sys
import tempfile
from webcapture.sources.responses import HttpResponse
from webcapture.archive.schema import ArchiveStore
from webcapture.batches.collection import collect_batch
from webcapture.batches.catalog import BatchCatalog, render_delivery_catalog
from webcapture.capture.service import capture_styles
from webcapture.delivery.reading import read_capture
from webcapture.delivery.verification import verify_capture
from webcapture.delivery.html_export import export_capture
from webcapture.html.encoding import decode_html
from webcapture.html.document import parse_document
from webcapture.html.references import find_image_occurrences
from webcapture.html.css import scan_css
from webcapture.html.stylesheets import find_style_references


def response_event(url, body=b"", *, final=None, status=200, headers=()):
    return {"request_url": url, "final_url": final or url, "status": status,
            "headers": [list(pair) for pair in headers], "body_hex": body.hex()}


class FixtureHttp:
    def __init__(self, events, *, alternatives=()):
        self.events = tuple(dict(event) for event in events)
        self.alternatives = {event["request_url"]: dict(event) for event in alternatives}
        self.position = 0
        self.calls = []
        self.enabled = True

    def request(self, request):
        if not self.enabled:
            raise RuntimeError("fixture source is offline")
        self.calls.append(request.url)
        if self.position < len(self.events) and request.url == self.events[self.position]["request_url"]:
            event = self.events[self.position]
            self.position += 1
        elif request.url in self.alternatives:
            event = self.alternatives[request.url]
        else:
            raise AssertionError(f"unexpected or out-of-order request: {request.url}")
        if ("body_text" in event) == ("body_hex" in event):
            raise ValueError("fixture requires exactly one body representation")
        body = (event["body_text"].encode("utf-8") if "body_text" in event
                else bytes.fromhex(event["body_hex"]))
        return HttpResponse(request.url, event["final_url"], event["status"],
                            tuple(tuple(pair) for pair in event["headers"]), body)

    def assert_consumed(self):
        if self.position != len(self.events):
            raise AssertionError("fixture has unconsumed requests")


def resolve_local(directory: Path, document: str, reference: str) -> str:
    """Return the delivered file a rewritten reference from ``document`` names.

    Fragments are dropped; the path is resolved relative to the referring file
    exactly as an offline reader would.
    """
    path = reference.split("#", 1)[0]
    resolved = posixpath.normpath(
        posixpath.join(posixpath.dirname(document) or ".", path))
    if resolved.startswith("../") or resolved.startswith("/"):
        raise AssertionError(f"reference escapes the delivery root: {reference}")
    return resolved


def fragment_of(value: str) -> str:
    return value.split("#", 1)[1] if "#" in value else ""


def collect_style_output(directory: Path, entry: str) -> dict:
    """Walk a delivered style capture by role without the archive parser.

    Returns the delivered files plus, for the HTML document and every CSS file
    reachable from it, each reference's resolved local target and that file's
    exact bytes.
    """
    files = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    observed = {"html": [], "css": []}
    html_text = files[posixpath.basename(entry)].decode("utf-8")
    document = parse_document(decode_html(html_text.encode("utf-8")))
    css_queue = []
    visited = set()
    for reference in find_style_references(document):
        local = resolve_local(directory, entry, reference.source)
        observed["html"].append({"kind": reference.kind,
                                 "fragment": fragment_of(reference.source),
                                 "file": local, "body": files[local]})
        if reference.kind == "link" and local not in css_queue:
            css_queue.append(local)
    while css_queue:
        name = css_queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        for token in scan_css(files[name].decode("utf-8")):
            local = resolve_local(directory, name, token.decoded)
            observed["css"].append({"document": name, "kind": token.kind,
                                    "fragment": fragment_of(token.decoded),
                                    "file": local, "body": files[local]})
            if token.kind == "import" and local not in visited:
                css_queue.append(local)
    return {"files": files, "observed": observed}


def demo_styles(work: Path) -> dict:
    """Capture a style edition, then reopen and deliver it with the source off."""
    root = Path(__file__).resolve().parent
    examples = json.loads((root / "style_examples.json").read_text(encoding="utf-8"))
    port = FixtureHttp([
        {"request_url": item["request"], "final_url": item["final"], "status": 200,
         "headers": [], "body_text": item["body"]}
        for item in examples["responses"]])
    # Hand-derived from style_examples.json: responses[0] is the HTML, the CSS
    # documents are responses[1] then responses[2] (a cycle through the /go/
    # redirect alias), and the binary targets are "P" then "I".
    expected_files = {
        "index.html": (b'<link rel="stylesheet" href="style-0000.css">'
                       b'<style>p{background:url(media-0001.bin#h)}</style>'),
        "style-0000.css": b'@import "style-0001.css";a{background:url(media-0000.bin#mark)}',
        "style-0001.css": b'@import "style-0000.css";b{content:"url(fake)"}',
        "media-0000.bin": b"P",
        "media-0001.bin": b"I",
    }
    db = work / "styles.sqlite"
    port.enabled = True
    port.calls.clear()
    port.position = 0
    with ArchiveStore(db) as archive:
        capture_id = capture_styles(archive, port, examples["entry"])
    assert port.calls == examples["expected"]["requests_each_once"], port.calls
    captured_calls = list(port.calls)
    port.enabled = False
    before_offline = len(port.calls)

    with ArchiveStore(db) as archive:
        verified = verify_capture(archive, capture_id)
        assert verified.kind == "styles"
        target = work / "style-delivery"
        entry = export_capture(archive, capture_id, target)
        collected = collect_style_output(target, entry)
        offline_calls = port.calls[before_offline:]

    expected_html = [
        {"kind": "link", "fragment": "", "file": "style-0000.css",
         "body": expected_files["style-0000.css"]},
        {"kind": "url", "fragment": "h", "file": "media-0001.bin", "body": b"I"},
    ]
    expected_css = [
        {"document": "style-0000.css", "kind": "import", "fragment": "",
         "file": "style-0001.css", "body": expected_files["style-0001.css"]},
        {"document": "style-0000.css", "kind": "url", "fragment": "mark",
         "file": "media-0000.bin", "body": b"P"},
        {"document": "style-0001.css", "kind": "import", "fragment": "",
         "file": "style-0000.css", "body": expected_files["style-0000.css"]},
    ]
    assert collected["observed"]["html"] == expected_html, collected["observed"]["html"]
    assert collected["observed"]["css"] == expected_css, collected["observed"]["css"]
    assert collected["files"] == expected_files, collected["files"]
    with ArchiveStore(db) as archive:
        assert verify_capture(archive, capture_id).kind == "styles"
        assert read_capture(archive, capture_id).html.response.body == \
            examples["responses"][0]["body"].encode("utf-8")
    return {
        "case_id": "styles",
        "input": {"entry": examples["entry"], "source_offline": True},
        "expected": {"requests": examples["expected"]["requests_each_once"],
                     "files": sorted(expected_files),
                     "binary_bodies": ["P", "I"],
                     "offline_requests": []},
        "actual": {"requests": captured_calls,
                   "files": sorted(collected["files"]),
                   "binary_bodies": [collected["files"]["media-0000.bin"].decode(),
                                     collected["files"]["media-0001.bin"].decode()],
                   "offline_requests": offline_calls},
        "code_paths": ["webcapture/capture/styles.py", "webcapture/html/css.py",
                       "webcapture/html/stylesheets.py",
                       "webcapture/archive/style_manifests.py",
                       "webcapture/delivery/style_paths.py",
                       "webcapture/delivery/style_export.py"],
        "oracle": "Hand-resolved request graph and reference targets from "
                  "style_examples.json; reopened after stopping the source; delivered "
                  "files read back by role.",
    }


def demo():
    root = Path(__file__).resolve().parent
    fixture = json.loads((root / "fixtures/http.json").read_text(encoding="utf-8"))
    port = FixtureHttp(fixture["events"])
    with tempfile.TemporaryDirectory(prefix="webcapture-demo-") as directory:
        work = Path(directory)
        db = work / "archive.sqlite"
        with ArchiveStore(db) as archive:
            result = collect_batch(archive, port, fixture["sources"])
            port.assert_consumed()
            original = verify_capture(archive, result.capture_ids[0]).view.html.response.body
        port.enabled = False
        with ArchiveStore(db) as archive:
            rows = BatchCatalog(archive).read_catalog(result.batch_id)
            links = {}
            image_bodies = []
            for row in rows:
                verified = verify_capture(archive, row.capture_id)
                target = work / f"capture-{row.ordinal:04d}"
                entry = export_capture(archive, row.capture_id, target)
                links[row.entry_id] = (row.capture_id, target.name + "/" + entry)
                derived = (target / entry).read_bytes()
                for occurrence in find_image_occurrences(parse_document(decode_html(derived))):
                    image_bodies.append((target / occurrence.source.split("#", 1)[0]).read_bytes().hex())
                assert verified.view.html.response.body == (
                    original if row.ordinal == 0 else b"<p>No illustrations today.</p>")
            catalog = render_delivery_catalog(rows, links)
            (work / "index.html").write_text(catalog, encoding="utf-8")
            expected_calls = ["https://river.test/today", "https://river.test/bulletins/day/pic.png",
                              "https://river.test/bulletins/day/empty.bin", "https://river.test/second"]
            expected_images = [fixture["events"][1]["body_hex"], fixture["events"][1]["body_hex"], ""]
            assert port.calls == expected_calls
            assert image_bodies == expected_images
            assert [row.entry_id for row in rows] == ["water-level", "notice"]
            assert catalog.index("water-level") < catalog.index("notice")
            assert verify_capture(archive, result.capture_ids[0]).view.html.response.body == original
            page_scenario = {
                "case_id": "v4",
                "input": {"sources": fixture["sources"], "source_offline": True},
                "expected": {"requests": expected_calls, "entries": ["water-level", "notice"]},
                "actual": {"requests": port.calls, "entries": [row.entry_id for row in rows]},
                "downstream_expected": {"image_bodies": expected_images,
                                        "original_unchanged": True},
                "downstream_actual": {
                    "image_bodies": image_bodies,
                    "original_unchanged": verify_capture(
                        archive, result.capture_ids[0]).view.html.response.body == original},
                "code_paths": ["webcapture/capture/service.py", "webcapture/capture/resources.py",
                               "webcapture/archive/publication.py",
                               "webcapture/delivery/verification.py",
                               "webcapture/delivery/html_export.py",
                               "webcapture/batches/catalog.py"],
                "oracle": "Hand-resolved final HTML directory, fixture image bytes, ordered "
                          "input IDs; read exported local files after closing and reopening "
                          "SQLite.",
            }
        print("POINTWISE_SCENARIO " + json.dumps(page_scenario, ensure_ascii=False,
                                                 sort_keys=True))
        style_scenario = demo_styles(work)
        print("POINTWISE_SCENARIO " + json.dumps(style_scenario, ensure_ascii=False,
                                                 sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description="River research evidence archive")
    parser.add_argument("command", choices=("verify", "demo"))
    args = parser.parse_args()
    if args.command == "verify":
        return subprocess.run([sys.executable, "-B", "-m", "unittest", "discover",
                               "-s", "tests", "-v"], cwd=Path(__file__).resolve().parent).returncode
    demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
