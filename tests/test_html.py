import json
from pathlib import Path
import unittest
from run import FixtureHttp, response_event
from webcapture.archive.schema import ArchiveStore
from webcapture.capture.service import capture_page
from webcapture.capture.jobs import CaptureError
from webcapture.html.encoding import decode_html
from webcapture.html.document import parse_document
from webcapture.html.references import find_image_occurrences


class TestHtmlInterpretation(unittest.TestCase):
    def test_invalid_utf8(self):
        with ArchiveStore(":memory:") as archive:
            port = FixtureHttp([response_event("https://e.test/page", b"\xff<img src='p'>")])
            with self.assertRaises(CaptureError):
                capture_page(archive, port, "https://e.test/page")
            self.assertEqual(archive.connection.execute("SELECT count(*) FROM publications").fetchone()[0], 0)
            self.assertEqual(archive.connection.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)
            self.assertEqual(len(port.calls), 1)

    def test_unicode_positions_and_literals(self):
        text = ('中文\n<!-- <img src="fake"> -->plain src="fake"'
                '<script>const x=\'<img src="fake">\';</script>'
                '<style>x{content:\'<img src="fake">\'}</style>'
                '<IMG SRC="p?a=1&amp;b=2"><img src=\'p\'><img src=p><img alt="none">')
        decoded = decode_html(text.encode())
        occurrences = find_image_occurrences(parse_document(decoded))
        self.assertEqual([o.source for o in occurrences], ["p?a=1&b=2", "p", "p"])
        self.assertEqual([o.token for o in occurrences], ['"p?a=1&amp;b=2"', "'p'", "p"])
        for occurrence in occurrences:
            self.assertEqual(decoded.text[occurrence.start:occurrence.end], occurrence.token)
            start, end = decoded.byte_offset(occurrence.start), decoded.byte_offset(occurrence.end)
            self.assertEqual(decoded.original[start:end].decode(), occurrence.token)
            self.assertEqual(decoded.character_offset(start), occurrence.start)
        self.assertGreater(decoded.byte_offset(occurrences[0].start), occurrences[0].start)
        with self.assertRaises(ValueError):
            decoded.character_offset(1)

    def test_reject_ambiguous_and_empty(self):
        for text in ('<img src="p" SRC="q">', '<img src="">', '<img src>'):
            with self.assertRaises(ValueError):
                find_image_occurrences(parse_document(decode_html(text.encode())))

    def test_lexical_material(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] /
                              "fixtures/lexical.json").read_text(encoding="utf-8"))
        for case in fixture["html"]:
            occurrences = find_image_occurrences(parse_document(decode_html(case["text"].encode())))
            self.assertEqual([o.source for o in occurrences], case["sources"])
        for case in fixture["css"]["valid"]:
            last = 0
            for token in case["tokens"]:
                self.assertGreaterEqual(token["start"], last)
                self.assertEqual(case["text"][token["start"]:token["end"]], token["raw"])
                self.assertIn(token["kind"], ("import", "url"))
                last = token["end"]

    def test_unquoted_query(self):
        occurrences = find_image_occurrences(parse_document(decode_html(
            b"<img src=p?a=1&amp;b=2>")))
        self.assertEqual(occurrences[0].source, "p?a=1&b=2")
        self.assertEqual(occurrences[0].token, "p?a=1&amp;b=2")
