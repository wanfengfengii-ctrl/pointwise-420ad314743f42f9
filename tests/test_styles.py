import json
from pathlib import Path
import tempfile
import unittest
from run import FixtureHttp
from webcapture.archive.schema import ArchiveStore, IntegrityError
from webcapture.capture.jobs import CaptureError
from webcapture.capture.service import capture_page, capture_styles
from webcapture.delivery.html_export import export_capture, write_file_exclusive
from webcapture.delivery.reading import read_capture
from webcapture.delivery.verification import verify_capture
from webcapture.html.css import scan_css
from webcapture.html.encoding import decode_html
from webcapture.html.document import parse_document
from webcapture.html.stylesheets import find_style_references

ROOT = Path(__file__).resolve().parents[1]


def load_examples():
    return json.loads((ROOT / "style_examples.json").read_text(encoding="utf-8"))


def event(request_url, body=b"", *, final=None, status=200, headers=()):
    return {"request_url": request_url, "final_url": final or request_url,
            "status": status, "headers": [list(pair) for pair in headers],
            "body_hex": body.hex()}


class StyleCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.db = self.work / "archive.sqlite"
        self.archive = ArchiveStore(self.db)
        self.addCleanup(lambda: self.archive.close())

    def reopen(self):
        self.archive.close()
        self.archive = ArchiveStore(self.db)

    def export(self, capture_id, name="out", writer=write_file_exclusive):
        target = self.work / name
        entry = export_capture(self.archive, capture_id, target, writer=writer)
        return target, entry, {path.name: path.read_bytes() for path in target.iterdir()}


class TestCssLexicalSubset(unittest.TestCase):
    def test_fixture_tokens(self):
        css = json.loads((ROOT / "fixtures/lexical.json").read_text(encoding="utf-8"))["css"]
        for case in css["valid"]:
            tokens = scan_css(case["text"])
            actual = [(token.kind, token.start, token.end, token.raw, token.decoded)
                      for token in tokens]
            expected = [(token["kind"], token["start"], token["end"],
                         token["raw"], token["decoded"]) for token in case["tokens"]]
            self.assertEqual(actual, expected, case["text"])

    def test_fixture_invalid(self):
        css = json.loads((ROOT / "fixtures/lexical.json").read_text(encoding="utf-8"))["css"]
        for case in css["invalid"]:
            with self.assertRaises(ValueError, msg=case["reason"]):
                scan_css(case["text"])

    def test_extra_lexical_edges(self):
        # Hand-checked spans: unquoted URL keeps raw text, quoted keeps quotes.
        tokens = scan_css('a{background:url( icons/p.png )}')
        self.assertEqual([(t.kind, t.start, t.end, t.raw, t.decoded) for t in tokens],
                         [("url", 18, 29, "icons/p.png", "icons/p.png")])
        tokens = scan_css('@import url("a.css") ;')
        self.assertEqual([(t.kind, t.raw, t.decoded) for t in tokens],
                         [("import", '"a.css"', "a.css")])
        for bad in ("a{background:url(a\tb)}", '@import url("a.css") print;',
                    "a{background:url(a(b))}", "a{background:url(a\\)}",
                    "a{background:url('a' b)}", "a{background:url(\\110000)}"):
            with self.assertRaises(ValueError):
                scan_css(bad)


class TestStyleEditionGraph(StyleCase):
    def build(self):
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        return data, FixtureHttp(events)

    def test_hand_computed_request_graph(self):
        data, port = self.build()
        capture_id = capture_styles(self.archive, port, data["entry"])
        port.assert_consumed()
        view = read_capture(self.archive, capture_id)
        manifest = view.manifest
        self.assertEqual(view.kind, "styles")
        self.assertEqual([resource.request_url for resource in manifest.resources],
                         data["expected"]["requests_each_once"])
        # Response bases are stored separately from request identities.
        evidence = {resource.request_url: view.evidence[resource.evidence_id].response
                    for resource in manifest.resources}
        self.assertEqual(evidence["https://example.test/notices/start"].final_url,
                         "https://example.test/docs/main.html")
        self.assertEqual(evidence["https://example.test/go/a.css"].final_url,
                         "https://example.test/assets/a.css")
        self.assertEqual(data["expected"]["external_css_base"],
                         evidence["https://example.test/go/a.css"].final_url)
        self.assertEqual(data["expected"]["inline_base"],
                         evidence["https://example.test/notices/start"].final_url)
        self.assertEqual(list(manifest.documents[1:]),
                         ["css:" + url for url in data["expected"]["css_cycle"]])
        # Binary targets: body and preserved fragment, in first-request order.
        binary = [(view.evidence[resource.evidence_id].response.body.decode(),
                   next(reference.fragment for reference in manifest.references
                        if reference.request_url == resource.request_url))
                  for resource in manifest.resources if "binary" in resource.roles]
        self.assertEqual(binary, [(item["body"], item["fragment"])
                                  for item in data["expected"]["binary_targets"]])
        self.assertFalse(data["expected"]["fake_url_requested"])
        self.assertFalse(any("fake" in url for url in data["expected"]["requests_each_once"]))

    def test_hand_computed_export(self):
        data, port = self.build()
        capture_id = capture_styles(self.archive, port, data["entry"])
        target, entry, files = self.export(capture_id)
        self.assertEqual(entry, "index.html")
        self.assertEqual(files, {
            "index.html":
                b'<link rel="stylesheet" href="style-0000.css">'
                b'<style>p{background:url(media-0001.bin#h)}</style>',
            "style-0000.css":
                b'@import "style-0001.css";a{background:url(media-0000.bin#mark)}',
            "style-0001.css":
                b'@import "style-0000.css";b{content:"url(fake)"}',
            "media-0000.bin": b"P",
            "media-0001.bin": b"I",
        })
        # The originals remain byte-identical in the archive.
        view = read_capture(self.archive, capture_id)
        self.assertEqual(view.evidence[view.manifest.resources[0].evidence_id].response.body,
                         data["responses"][0]["body"].encode("utf-8"))
        self.assertEqual([resource.evidence_id for resource in view.manifest.resources],
                         sorted(resource.evidence_id for resource in view.manifest.resources))

    def test_offline_reopen_verify_export(self):
        data, port = self.build()
        capture_id = capture_styles(self.archive, port, data["entry"])
        calls = list(port.calls)
        port.enabled = False
        self.reopen()
        verified = verify_capture(self.archive, capture_id)
        self.assertEqual(verified.kind, "styles")
        self.assertEqual(verified.html.text, data["responses"][0]["body"])
        target, entry, files = self.export(capture_id, "reopened")
        self.assertEqual(files["media-0000.bin"], b"P")
        self.assertEqual(files["media-0001.bin"], b"I")
        self.assertEqual(port.calls, calls)

    def test_alias_requests_are_not_merged(self):
        shared = "https://example.test/assets/shared.css"
        html = b'<link rel="stylesheet" href="red.css"><link rel="stylesheet" href="blue.css">'
        port = FixtureHttp([
            event("https://example.test/index", html),
            event("https://example.test/red.css", b"a{color:red}", final=shared),
            event("https://example.test/blue.css", b"a{color:blue}", final=shared)])
        capture_id = capture_styles(self.archive, port, "https://example.test/index")
        port.assert_consumed()
        view = read_capture(self.archive, capture_id)
        bodies = [view.evidence[resource.evidence_id].response.body
                  for resource in view.manifest.resources if "css" in resource.roles]
        self.assertEqual(bodies, [b"a{color:red}", b"a{color:blue}"])
        target, entry, files = self.export(capture_id)
        self.assertEqual(files["style-0000.css"], b"a{color:red}")
        self.assertEqual(files["style-0001.css"], b"a{color:blue}")
        self.assertIn(b'href="style-0000.css"><link rel="stylesheet" href="style-0001.css"',
                      files["index.html"])

    def test_repeated_and_root_references(self):
        html = b'<img src="/page#self"><link rel="stylesheet" href="/page#again">'
        port = FixtureHttp([event("https://example.test/page", html)])
        capture_id = capture_styles(self.archive, port, "https://example.test/page")
        port.assert_consumed()
        view = read_capture(self.archive, capture_id)
        self.assertEqual(len(view.manifest.resources), 1)
        self.assertEqual(set(view.manifest.resources[0].roles), {"html", "binary", "css"})
        self.assertEqual([reference.fragment for reference in view.manifest.references],
                         ["self", "again"])
        target, entry, files = self.export(capture_id)
        self.assertEqual(files["index.html"],
                         b'<img src="media-0000.bin#self">'
                         b'<link rel="stylesheet" href="style-0000.css#again">')
        self.assertEqual(files["media-0000.bin"], html)
        self.assertEqual(files["style-0000.css"], html)

    def test_cycle_closure_roles(self):
        port = FixtureHttp([
            event("https://example.test/i", b'<link rel="stylesheet" href="x.css">'),
            event("https://example.test/x.css", b'@import "y.css";a{background:url(y.css)}'),
            event("https://example.test/y.css", b'@import "x.css";b{background:url(x.css)}')])
        capture_id = capture_styles(self.archive, port, "https://example.test/i")
        port.assert_consumed()
        view = read_capture(self.archive, capture_id)
        roles = {resource.request_url: set(resource.roles)
                 for resource in view.manifest.resources}
        self.assertEqual(roles["https://example.test/x.css"], {"css", "binary"})
        self.assertEqual(roles["https://example.test/y.css"], {"css", "binary"})
        target, entry, files = self.export(capture_id)
        # Both documents are delivered; each role gets its own local copy.
        # Binary roles are first assigned while interpreting y.css, so x.css
        # (the import target there) becomes media-0000 and y.css media-0001.
        self.assertEqual(files["style-0000.css"],
                         b'@import "style-0001.css";a{background:url(media-0001.bin)}')
        self.assertEqual(files["style-0001.css"],
                         b'@import "style-0000.css";b{background:url(media-0000.bin)}')
        self.assertEqual(files["media-0000.bin"],
                         b'@import "y.css";a{background:url(y.css)}')
        self.assertEqual(files["media-0001.bin"],
                         b'@import "x.css";b{background:url(x.css)}')

    def test_inline_contexts_and_escapes(self):
        html = ('<style>/* url(fake) */ @import "c.css";p{background:url( i\\6d g.png#f )}</style>'
                '<div style="background:url(&quot;q&amp;r.png&quot;);color:red"></div>'
                '<div style=background:url(z.png)></div>').encode()
        port = FixtureHttp([
            event("https://example.test/d/i", html),
            event("https://example.test/d/c.css", b'c{}'),
            event("https://example.test/d/img.png", b"IMG"),
            event("https://example.test/d/q&r.png", b"QR"),
            event("https://example.test/d/z.png", b"Z")])
        capture_id = capture_styles(self.archive, port, "https://example.test/d/i")
        port.assert_consumed()
        view = read_capture(self.archive, capture_id)
        sources = [reference.source for reference in view.manifest.references]
        self.assertEqual(sources, ["c.css", "img.png#f", "q&r.png", "z.png"])
        self.assertEqual([reference.fragment for reference in view.manifest.references],
                         ["", "f", "", ""])
        target, entry, files = self.export(capture_id)
        self.assertEqual(files["index.html"],
                         b'<style>/* url(fake) */ @import "style-0000.css";'
                         b'p{background:url( media-0000.bin#f )}</style>'
                         b'<div style="background:url(&quot;media-0001.bin&quot;);color:red"></div>'
                         b'<div style=background:url(media-0002.bin)></div>')
        self.assertEqual(files["media-0000.bin"], b"IMG")
        self.assertEqual(files["media-0001.bin"], b"QR")
        self.assertEqual(files["media-0002.bin"], b"Z")

    def test_same_request_binary_and_css(self):
        body = b"a{color:red}"
        html = b'<link rel="stylesheet" href="s.css"><img src="s.css">'
        port = FixtureHttp([event("https://example.test/p", html),
                            event("https://example.test/s.css", body)])
        capture_id = capture_styles(self.archive, port, "https://example.test/p")
        self.assertEqual(port.calls.count("https://example.test/s.css"), 1)
        target, entry, files = self.export(capture_id)
        self.assertEqual(files["style-0000.css"], body)
        self.assertEqual(files["media-0000.bin"], body)


class TestStyleFailures(StyleCase):
    def test_missing_dependency_keeps_previous(self):
        old = capture_page(self.archive, FixtureHttp([
            event("https://example.test/old", b"old evidence")]), "https://example.test/old")
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        events[2]["status"] = 404
        with self.assertRaises(CaptureError):
            capture_styles(self.archive, FixtureHttp(events), data["entry"])
        self.assertEqual(self.archive.connection.execute(
            "SELECT count(*) FROM publications").fetchone()[0], 1)
        self.assertEqual(self.archive.connection.execute(
            "SELECT count(*) FROM evidence").fetchone()[0], 1)
        target, entry, files = self.export(old, "old")
        self.assertEqual(files["index.html"], b"old evidence")
        self.assertEqual(verify_capture(self.archive, old).kind, "page")

    def test_tampered_evidence_rejected_offline(self):
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        port = FixtureHttp(events)
        capture_id = capture_styles(self.archive, port, data["entry"])
        port.enabled = False
        self.archive.connection.execute("UPDATE evidence SET body=? WHERE request_url=?",
                                        (b"tampered", "https://example.test/go/a.css"))
        self.reopen()
        for call in (lambda: verify_capture(self.archive, capture_id),
                     lambda: export_capture(self.archive, capture_id, self.work / "bad")):
            with self.assertRaises(IntegrityError):
                call()
        self.assertFalse((self.work / "bad").exists())
        self.assertEqual(port.calls, data["expected"]["requests_each_once"])

    def test_dangling_manifest_payload_rejected(self):
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        capture_id = capture_styles(self.archive, FixtureHttp(events), data["entry"])
        row = self.archive.connection.execute(
            "SELECT payload FROM manifests WHERE capture_id=?", (capture_id,)).fetchone()
        payload = json.loads(row["payload"])
        payload["references"][1]["request_url"] = "https://example.test/absent"
        self.archive.connection.execute("UPDATE manifests SET payload=? WHERE capture_id=?",
                                        (json.dumps(payload, ensure_ascii=False,
                                                    sort_keys=True, separators=(",", ":")),
                                         capture_id))
        with self.assertRaises(IntegrityError):
            verify_capture(self.archive, capture_id)
        with self.assertRaises(IntegrityError):
            export_capture(self.archive, capture_id, self.work / "dangling")
        self.assertFalse((self.work / "dangling").exists())

    def test_missing_evidence_row(self):
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        capture_id = capture_styles(self.archive, FixtureHttp(events), data["entry"])
        self.archive.connection.execute(
            "DELETE FROM evidence WHERE request_url=?", ("https://example.test/assets/b.css",))
        with self.assertRaises(IntegrityError):
            read_capture(self.archive, capture_id)
        with self.assertRaises(IntegrityError):
            verify_capture(self.archive, capture_id)
        with self.assertRaises(IntegrityError):
            export_capture(self.archive, capture_id, self.work / "missing")

    def test_rejected_html_lexicon(self):
        cases = [
            b'<link rel="stylesheet" href="a.css" href="b.css">',
            b'<link rel="stylesheet">',
            b'<div style="a:1" style="b:2"></div>',
            b'<style>a{background:url("unterminated)}</style>',
            b'<style>a{background:url()}</style>',
            b'<style>@import "x.css" screen;</style>',
            b'<style>a{}</style',
            b'<img src=""><link rel="stylesheet" href="a.css">',
            b'<style>@media print{a{background:url(x.png)}}</style>',
            b'<style>@font-face{src:url(f.woff)}</style>',
            b'<link rel="stylesheet" href="a.css#x" href="a.css#y">',
        ]
        for ordinal, body in enumerate(cases):
            port = FixtureHttp([event("https://example.test/%d" % ordinal, body)])
            with self.assertRaises(CaptureError, msg=body):
                capture_styles(self.archive, port, "https://example.test/%d" % ordinal)
            self.assertEqual(port.calls, ["https://example.test/%d" % ordinal])
        self.assertEqual(self.archive.connection.execute(
            "SELECT count(*) FROM publications").fetchone()[0], 0)

    def test_publish_transaction_failure(self):
        old = capture_page(self.archive, FixtureHttp([
            event("https://example.test/old", b"old evidence")]), "https://example.test/old")
        self.archive.connection.execute(
            "CREATE TRIGGER fail_manifest BEFORE INSERT ON manifests "
            "BEGIN SELECT RAISE(ABORT,'controlled manifest write failure'); END")
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        with self.assertRaises(CaptureError):
            capture_styles(self.archive, FixtureHttp(events), data["entry"])
        self.assertEqual(self.archive.connection.execute(
            "SELECT count(*) FROM publications").fetchone()[0], 1)
        self.assertEqual(self.archive.connection.execute(
            "SELECT count(*) FROM evidence").fetchone()[0], 1)
        self.assertEqual(self.archive.connection.execute(
            "SELECT count(*) FROM manifests").fetchone()[0], 1)
        self.assertFalse(self.archive.connection.in_transaction)
        self.archive.connection.execute("DROP TRIGGER fail_manifest")
        self.assertEqual(verify_capture(self.archive, old).kind, "page")
        capture_id = capture_styles(self.archive, FixtureHttp(events), data["entry"])
        self.assertEqual(verify_capture(self.archive, capture_id).kind, "styles")

    def test_zero_byte_binary_delivered(self):
        html = b'<style>a{background:url(zero.bin)}</style>'
        port = FixtureHttp([event("https://example.test/p", html),
                            event("https://example.test/zero.bin", b"")])
        capture_id = capture_styles(self.archive, port, "https://example.test/p")
        view = read_capture(self.archive, capture_id)
        self.assertEqual(view.images[0].response.body, b"")
        target, entry, files = self.export(capture_id)
        self.assertEqual(files["media-0000.bin"], b"")
        self.assertEqual(files["index.html"],
                         b'<style>a{background:url(media-0000.bin)}</style>')

    def test_writer_failure_cleanup(self):
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        capture_id = capture_styles(self.archive, FixtureHttp(events), data["entry"])
        neighbour = self.work / "user.txt"
        neighbour.write_bytes(b"user")
        written = []

        def failing_disk(path, body):
            if written:
                raise OSError("controlled second-file storage failure")
            write_file_exclusive(path, body)
            written.append(path)

        with self.assertRaises(OSError):
            export_capture(self.archive, capture_id, self.work / "failed",
                           writer=failing_disk)
        self.assertEqual(len(written), 1)
        self.assertFalse((self.work / "failed").exists())
        self.assertEqual(neighbour.read_bytes(), b"user")
        target, entry, files = self.export(capture_id, "after-failure")
        self.assertIn("media-0000.bin", files)
        self.assertEqual(verify_capture(self.archive, capture_id).kind, "styles")

    def test_unsupported_and_pseudo_targets(self):
        html = (b'<style>a{content:"url(fake)";x:url(data:image/png,x)}</style>')
        port = FixtureHttp([event("https://example.test/p", html)])
        with self.assertRaises(CaptureError):
            capture_styles(self.archive, port, "https://example.test/p")
        self.assertEqual(port.calls, ["https://example.test/p"])
        # A pseudo reference alone is no reference at all.
        port = FixtureHttp([event("https://example.test/q",
                                  b'<style>a{content:"url(fake)"}</style>')])
        capture_id = capture_styles(self.archive, port, "https://example.test/q")
        self.assertEqual(port.calls, ["https://example.test/q"])
        view = read_capture(self.archive, capture_id)
        self.assertEqual(view.manifest.references, ())
        target, entry, files = self.export(capture_id)
        self.assertEqual(files["index.html"], b'<style>a{content:"url(fake)"}</style>')

    def test_invalid_entry_and_target(self):
        for url in ("file:///tmp/x", "https://e.test/a b", ""):
            with self.assertRaises(CaptureError):
                capture_styles(self.archive, FixtureHttp([]), url)
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        capture_id = capture_styles(self.archive, FixtureHttp(events), data["entry"])
        existing = self.work / "existing"
        existing.mkdir()
        (existing / "note").write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            export_capture(self.archive, capture_id, existing)
        self.assertEqual((existing / "note").read_bytes(), b"keep")


class TestCombinedEditions(StyleCase):
    """A V1 capture and a style capture coexist in one archive."""

    def test_both_editions_survive_one_archive(self):
        page = capture_page(self.archive, FixtureHttp([
            event("https://example.test/page", b'<img src="p.png">'),
            event("https://example.test/p.png", b"PNG")]), "https://example.test/page")
        data = load_examples()
        events = [event(item["request"], item["body"].encode("utf-8"), final=item["final"])
                  for item in data["responses"]]
        port = FixtureHttp(events)
        styled = capture_styles(self.archive, port, data["entry"])
        port.enabled = False
        self.reopen()
        self.assertEqual(verify_capture(self.archive, page).kind, "page")
        self.assertEqual(verify_capture(self.archive, styled).kind, "styles")
        page_target, page_entry, page_files = self.export(page, "page-out")
        style_target, style_entry, style_files = self.export(styled, "style-out")
        self.assertEqual(page_files[page_entry], b'<img src="image-0000.bin">')
        self.assertEqual(page_files["image-0000.bin"], b"PNG")
        self.assertEqual(style_files["media-0000.bin"], b"P")
        # Reading one edition never pulls in the other's evidence.
        self.assertEqual(len(read_capture(self.archive, styled).evidence), 5)
        self.assertEqual(len(read_capture(self.archive, page).evidence), 2)

    def test_long_css_chain_is_iterative(self):
        length = 400
        events = [event("https://example.test/0.html",
                        b'<link rel="stylesheet" href="c0.css">')]
        for index in range(length):
            body = (f'@import "c{index + 1}.css";' if index + 1 < length else "") + \
                   f'a{{color:red}}'
            events.append(event(f"https://example.test/c{index}.css", body.encode()))
        port = FixtureHttp(events)
        capture_id = capture_styles(self.archive, port, "https://example.test/0.html")
        port.assert_consumed()
        self.assertEqual(len(port.calls), length + 1)
        target, entry, files = self.export(capture_id)
        self.assertEqual(len([name for name in files if name.startswith("style-")]), length)


class TestHtmlStyleVocabulary(unittest.TestCase):
    def references(self, text):
        document = parse_document(decode_html(text.encode("utf-8")))
        return find_style_references(document)

    def test_offsets_cover_value_tokens(self):
        text = '<img src="a.png"><link rel=stylesheet href=b.css><div style="x:url(c.png)">'
        references = self.references(text)
        self.assertEqual([(r.kind, r.context, r.token) for r in references],
                         [("image", "attribute", '"a.png"'),
                          ("link", "attribute", "b.css"),
                          ("url", "style_attribute", "c.png")])
        for reference in references:
            self.assertEqual(text[reference.start:reference.end], reference.token)

    def test_pseudo_and_script_ignored(self):
        text = ('<!-- <img src="x"> <link rel=stylesheet href=x.css> -->'
                '<script>"<img src=y><style>a{background:url(z)}</style>"</script>'
                '<p>link rel=stylesheet href=no.css</p>')
        self.assertEqual(self.references(text), ())

    def test_style_block_positions(self):
        text = 'x<style>p{background:url( p.png#f )}</style>y'
        (reference,) = self.references(text)
        self.assertEqual((reference.kind, reference.context), ("url", "style_text"))
        self.assertEqual(text[reference.start:reference.end], "p.png#f")
        self.assertEqual(reference.source, "p.png#f")


if __name__ == "__main__":
    unittest.main()
