import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from run import FixtureHttp, response_event
from webcapture.archive.schema import ArchiveStore
from webcapture.batches.collection import collect_batch
from webcapture.batches.catalog import BatchCatalog, render_delivery_catalog
from webcapture.batches.normalization import normalize_sources
from webcapture.batches.identity import canonical_batch_bytes, compute_batch_id
from webcapture.capture.jobs import CaptureError
from webcapture.delivery.reading import read_capture
from webcapture.delivery.html_export import export_capture


class TestDailyCapture(unittest.TestCase):
    def setUp(self):
        self.archive = ArchiveStore(":memory:")
        self.addCleanup(self.archive.close)

    def test_ordered_sources(self):
        entries = [{"entry_id": "z-first", "url": "https://e.test/a"},
                   {"entry_id": "a-second", "url": "https://e.test/b"}]
        http = FixtureHttp([response_event("https://e.test/a", b"A"),
                            response_event("https://e.test/b", b"B")])
        result = collect_batch(self.archive, http, entries)
        rows = BatchCatalog(self.archive).read_catalog(result.batch_id)
        self.assertEqual([r.entry_id for r in rows], ["z-first", "a-second"])
        self.assertEqual([read_capture(self.archive, r.capture_id).html.response.body for r in rows],
                         [b"A", b"B"])
        with tempfile.TemporaryDirectory() as directory:
            links = {}
            for row in rows:
                target = Path(directory) / str(row.ordinal)
                entry = export_capture(self.archive, row.capture_id, target)
                links[row.entry_id] = (row.capture_id, str(row.ordinal) + "/" + entry)
            rendered = render_delivery_catalog(rows, links)
            self.assertLess(rendered.index("z-first"), rendered.index("a-second"))
            self.assertEqual((Path(directory) / "0/index.html").read_bytes(), b"A")
            self.assertEqual((Path(directory) / "1/index.html").read_bytes(), b"B")
        http.assert_consumed()
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v1", "input": entries, "expected": ["z-first", "a-second"],
            "actual": [r.entry_id for r in rows], "downstream_expected": ["A", "B"],
            "downstream_actual": [read_capture(self.archive, r.capture_id).html.response.body.decode()
                                  for r in rows],
            "code_paths": ["webcapture/batches/identity.py", "webcapture/batches/collection.py",
                           "webcapture/batches/catalog.py", "webcapture/delivery/html_export.py"],
            "oracle": "Input order and two independently specified response bodies."}, sort_keys=True))

    def test_normalization(self):
        http = FixtureHttp([response_event("https://e.test/a?x=2&x=1", b"")])
        result = collect_batch(self.archive, http, [
            {"entry_id": " item ", "url": " https://e.test/a?x=2&x=1#part "}])
        row = BatchCatalog(self.archive).read_catalog(result.batch_id)[0]
        self.assertEqual((row.entry_id, row.url), ("item", "https://e.test/a?x=2&x=1"))
        for entries in (
            [{"entry_id": "x", "url": "https://e.test/a"},
             {"entry_id": " x ", "url": "https://e.test/b"}],
            [{"entry_id": "x", "url": "https://e.test/a"},
             {"entry_id": "y", "url": "file:///bad"}],
            [{"entry_id": 3, "url": "https://e.test/a"}],
        ):
            untouched = FixtureHttp([])
            with self.assertRaises(ValueError):
                collect_batch(self.archive, untouched, entries)
            self.assertEqual(untouched.calls, [])

    def test_empty_batch(self):
        port = FixtureHttp([])
        result = collect_batch(self.archive, port, [])
        self.assertEqual(result.batch_id, hashlib.sha256(b"[]").hexdigest())
        self.assertEqual(result.capture_ids, ())
        self.assertEqual(BatchCatalog(self.archive).read_catalog(result.batch_id), ())
        self.assertIn("<ul>", render_delivery_catalog((), {}))
        self.assertEqual(collect_batch(self.archive, port, []), result)
        self.assertEqual(port.calls, [])

    def test_identity_order(self):
        entries = [{"entry_id": "A|B", "url": "https://e.test/a"},
                   {"entry_id": "C", "url": "https://e.test/b"}]
        first = normalize_sources(entries)
        reverse = normalize_sources(list(reversed(entries)))
        expected = b'[["A|B","https://e.test/a"],["C","https://e.test/b"]]'
        self.assertEqual(canonical_batch_bytes(first), expected)
        self.assertEqual(compute_batch_id(first), hashlib.sha256(expected).hexdigest())
        self.assertNotEqual(compute_batch_id(first), compute_batch_id(reverse))
        http = FixtureHttp([response_event(e["url"], e["entry_id"].encode())
                            for e in entries + list(reversed(entries))])
        left = collect_batch(self.archive, http, entries)
        right = collect_batch(self.archive, http, list(reversed(entries)))
        self.assertNotEqual(left.batch_id, right.batch_id)
        self.assertEqual([r.entry_id for r in BatchCatalog(self.archive).read_catalog(right.batch_id)],
                         ["C", "A|B"])

    def test_identity_conflict(self):
        catalog = BatchCatalog(self.archive)
        first = normalize_sources([{"entry_id": "a", "url": "https://e.test/a"}])
        other = normalize_sources([{"entry_id": "b", "url": "https://e.test/b"}])
        self.assertEqual(catalog.reserve("controlled-collision", first, canonical_batch_bytes(first)), "new")
        with self.assertRaises(ValueError):
            catalog.reserve("controlled-collision", other, canonical_batch_bytes(other))
        row = self.archive.connection.execute("SELECT entry_id FROM batch_entries").fetchone()
        self.assertEqual(row[0], "a")

    def test_recollect(self):
        entries = [{"entry_id": "a", "url": "https://e.test/a"}]
        http = FixtureHttp([response_event("https://e.test/a", b"first")])
        original = collect_batch(self.archive, http, entries)
        http.enabled = False
        self.assertEqual(collect_batch(self.archive, http, entries), original)
        self.assertEqual(http.calls, ["https://e.test/a"])

    def test_same_url_entries(self):
        entries = [{"entry_id": "a", "url": "https://e.test/shared"},
                   {"entry_id": "b", "url": "https://e.test/shared"}]
        http = FixtureHttp([response_event("https://e.test/shared", b"first"),
                            response_event("https://e.test/shared", b"second")])
        result = collect_batch(self.archive, http, entries)
        rows = BatchCatalog(self.archive).read_catalog(result.batch_id)
        self.assertNotEqual(rows[0].capture_id, rows[1].capture_id)
        self.assertEqual([read_capture(self.archive, row.capture_id).html.response.body for row in rows],
                         [b"first", b"second"])
        self.assertEqual(http.calls, ["https://e.test/shared", "https://e.test/shared"])

    def test_partial_failure(self):
        entries = [{"entry_id": "good", "url": "https://e.test/a"},
                   {"entry_id": "bad", "url": "https://e.test/b"}]
        http = FixtureHttp([response_event("https://e.test/a", b"good"),
                            response_event("https://e.test/b", b"missing", status=404)])
        with self.assertRaises(CaptureError) as original:
            collect_batch(self.archive, http, entries)
        batch_id = compute_batch_id(normalize_sources(entries))
        rows = self.archive.connection.execute(
            "SELECT entry_id,capture_id FROM batch_entries ORDER BY ordinal").fetchall()
        self.assertIsNotNone(rows[0]["capture_id"])
        self.assertIsNone(rows[1]["capture_id"])
        self.assertEqual(read_capture(self.archive, rows[0]["capture_id"]).html.response.body, b"good")
        with self.assertRaises(ValueError):
            BatchCatalog(self.archive).read_catalog(batch_id)
        http.enabled = False
        with self.assertRaises(CaptureError) as replay:
            collect_batch(self.archive, http, entries)
        self.assertEqual(str(original.exception), str(replay.exception))
        self.assertEqual(len(http.calls), 2)

    def test_catalog_escaping_and_binding(self):
        entries = [{"entry_id": "<a>&", "url": "https://e.test/a"}]
        result = collect_batch(self.archive, FixtureHttp([response_event("https://e.test/a")]), entries)
        rows = BatchCatalog(self.archive).read_catalog(result.batch_id)
        text = render_delivery_catalog(rows, {"<a>&": (result.capture_ids[0], "0/index.html")})
        self.assertIn("&lt;a&gt;&amp;", text)
        for mapping in ({}, {"<a>&": ("wrong", "0/index.html")},
                        {"<a>&": (result.capture_ids[0], "../outside")}):
            with self.assertRaises(ValueError):
                render_delivery_catalog(rows, mapping)
