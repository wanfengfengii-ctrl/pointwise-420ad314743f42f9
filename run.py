import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from webcapture.sources.responses import HttpResponse
from webcapture.archive.schema import ArchiveStore
from webcapture.batches.collection import collect_batch
from webcapture.batches.catalog import BatchCatalog, render_delivery_catalog
from webcapture.delivery.verification import verify_capture
from webcapture.delivery.html_export import export_capture
from webcapture.html.encoding import decode_html
from webcapture.html.document import parse_document
from webcapture.html.references import find_image_occurrences


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
            print("POINTWISE_SCENARIO " + json.dumps({
                "case_id": "v4", "input": {"sources": fixture["sources"], "source_offline": True},
                "expected": {"requests": expected_calls, "entries": ["water-level", "notice"]},
                "actual": {"requests": port.calls, "entries": [row.entry_id for row in rows]},
                "downstream_expected": {"image_bodies": expected_images, "original_unchanged": True},
                "downstream_actual": {"image_bodies": image_bodies,
                                      "original_unchanged": verify_capture(
                                          archive, result.capture_ids[0]).view.html.response.body == original},
                "code_paths": ["webcapture/capture/service.py", "webcapture/capture/resources.py",
                               "webcapture/archive/publication.py", "webcapture/delivery/verification.py",
                               "webcapture/delivery/html_export.py", "webcapture/batches/catalog.py"],
                "oracle": "Hand-resolved final HTML directory, fixture image bytes, ordered input IDs; "
                          "read exported local files after closing and reopening SQLite."
            }, ensure_ascii=False, sort_keys=True))


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
