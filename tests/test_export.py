import hashlib
from pathlib import Path
import tempfile
import unittest
from run import FixtureHttp, response_event
from webcapture.archive.schema import ArchiveStore, IntegrityError
from webcapture.capture.service import capture_page
from webcapture.batches.collection import collect_batch
from webcapture.batches.catalog import BatchCatalog
from webcapture.delivery.reading import read_capture
from webcapture.delivery.verification import verify_capture
from webcapture.delivery.html_export import export_capture, write_file_exclusive
from webcapture.html.encoding import decode_html
from webcapture.html.document import parse_document
from webcapture.html.references import find_image_occurrences


class TestOfflineExport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.db = self.work / "archive.sqlite"
        self.archive = ArchiveStore(self.db)
        self.addCleanup(lambda: self.archive.close())

    def capture(self, body, images=()):
        events = [response_event("https://e.test/dir/page", body)]
        events.extend(response_event(url, content) for url, content in images)
        port = FixtureHttp(events)
        identity = capture_page(self.archive, port, "https://e.test/dir/page")
        port.assert_consumed()
        port.enabled = False
        return identity, port

    def reopen(self):
        self.archive.close()
        self.archive = ArchiveStore(self.db)

    def test_reopen_without_source(self):
        original = b'<p>notice</p><img src="p#mark">'
        identity, port = self.capture(original, [("https://e.test/dir/p", b"picture")])
        self.reopen()
        self.archive.initialize()
        verified = verify_capture(self.archive, identity)
        self.assertEqual(verified.view.html.response.body, original)
        target = self.work / "offline"
        entry = export_capture(self.archive, identity, target)
        derived = (target / entry).read_bytes()
        occurrence = find_image_occurrences(parse_document(decode_html(derived)))[0]
        self.assertEqual(occurrence.source, "image-0000.bin#mark")
        self.assertEqual((target / occurrence.source.split("#")[0]).read_bytes(), b"picture")
        self.assertEqual(read_capture(self.archive, identity).html.response.body, original)
        self.assertEqual(port.calls, ["https://e.test/dir/page", "https://e.test/dir/p"])
        with self.assertRaises(FileExistsError):
            export_capture(self.archive, identity, target)

    def test_reinitialize_preserves(self):
        identity, _ = self.capture(b"original")
        batch = collect_batch(self.archive, FixtureHttp([response_event("https://e.test/batch", b"batch")]),
                              [{"entry_id": "saved", "url": "https://e.test/batch"}])
        self.archive.connection.execute("CREATE TABLE user_notes(note TEXT)")
        self.archive.connection.execute("INSERT INTO user_notes VALUES('my annotation')")
        root = read_capture(self.archive, identity).html.evidence_id
        self.archive.connection.execute("UPDATE evidence SET body=? WHERE id=?", (b"user modification", root))
        self.archive.initialize()
        self.reopen()
        self.assertEqual(self.archive.connection.execute("SELECT note FROM user_notes").fetchone()[0],
                         "my annotation")
        self.assertEqual(read_capture(self.archive, identity).html.response.body, b"user modification")
        self.assertEqual(BatchCatalog(self.archive).read_catalog(batch.batch_id)[0].capture_id,
                         batch.capture_ids[0])
        with self.assertRaises(IntegrityError):
            verify_capture(self.archive, identity)
        self.archive.connection.execute("PRAGMA user_version=77")
        self.archive.close()
        with self.assertRaises(IntegrityError):
            ArchiveStore(self.db)
        # Restore only the test-owned version so cleanup retains an open store.
        import sqlite3
        with sqlite3.connect(self.db) as connection:
            connection.execute("PRAGMA user_version=1")
        self.archive = ArchiveStore(self.db)

    def test_path_collisions(self):
        identity, _ = self.capture(
            b'<img src="/x/a.png"><img src="/y/a.png"><img src="/%2e%2e/a.png">',
            [("https://e.test/x/a.png", b"X"), ("https://e.test/y/a.png", b"Y"),
             ("https://e.test/%2e%2e/a.png", b"Z")])
        target = self.work / "paths"
        entry = export_capture(self.archive, identity, target)
        occurrences = find_image_occurrences(parse_document(decode_html((target / entry).read_bytes())))
        self.assertEqual([o.source for o in occurrences],
                         ["image-0000.bin", "image-0001.bin", "image-0002.bin"])
        self.assertEqual([(target / o.source).read_bytes() for o in occurrences], [b"X", b"Y", b"Z"])
        self.assertEqual(len(list(target.iterdir())), 4)

    def test_literal_occurrences(self):
        original = ('中文<!-- p?a=1&b=2 -->text p?a=1&b=2'
                    '<script>const x=\'<img src="fake">\';</script>'
                    '<style>x{content:"p?a=1&b=2"}</style>'
                    '<img src="p?a=1&amp;b=2#one"><img src=\'p?a=1&amp;b=2#two\'>'
                    '<img src=p?a=1&amp;b=2>').encode()
        identity, _ = self.capture(original, [("https://e.test/dir/p?a=1&b=2", b"same")])
        before = hashlib.sha256(original).hexdigest()
        target = self.work / "literal"
        entry = export_capture(self.archive, identity, target)
        actual = (target / entry).read_text(encoding="utf-8")
        expected = ('中文<!-- p?a=1&b=2 -->text p?a=1&b=2'
                    '<script>const x=\'<img src="fake">\';</script>'
                    '<style>x{content:"p?a=1&b=2"}</style>'
                    '<img src="image-0000.bin#one"><img src="image-0000.bin#two">'
                    '<img src="image-0000.bin">')
        self.assertEqual(actual, expected)
        self.assertEqual(hashlib.sha256(read_capture(self.archive, identity).html.response.body).hexdigest(),
                         before)

    def test_existing_target(self):
        identity, _ = self.capture(b"page")
        target = self.work / "user"
        target.mkdir()
        (target / "notes").write_bytes(b"do not remove")
        with self.assertRaises(FileExistsError):
            export_capture(self.archive, identity, target)
        self.assertEqual((target / "notes").read_bytes(), b"do not remove")
        self.assertEqual(read_capture(self.archive, identity).html.response.body, b"page")

    def test_write_failure_cleanup(self):
        identity, _ = self.capture(b'<img src="p"><img src="q">',
                                  [("https://e.test/dir/p", b"P"), ("https://e.test/dir/q", b"Q")])
        neighbour = self.work / "user.txt"
        neighbour.write_bytes(b"user")
        target = self.work / "failed"
        written = []
        def failing_disk(path, body):
            if written:
                raise OSError("controlled second-file storage failure")
            write_file_exclusive(path, body)
            written.append(path)
        with self.assertRaises(OSError):
            export_capture(self.archive, identity, target, writer=failing_disk)
        self.assertEqual(len(written), 1)
        self.assertFalse(target.exists())
        self.assertEqual(neighbour.read_bytes(), b"user")
        self.assertEqual(verify_capture(self.archive, identity).view.images[0].response.body, b"P")
        self.assertEqual(export_capture(self.archive, identity, target), "index.html")

    def test_root_reuse_export_and_empty_page(self):
        for ordinal, body in enumerate((b'<img src="/dir/page#self">', b"")):
            identity, port = self.capture(body)
            target = self.work / f"root-{ordinal}"
            entry = export_capture(self.archive, identity, target)
            self.assertTrue((target / entry).is_file())
            if ordinal == 0:
                self.assertEqual((target / "image-0000.bin").read_bytes(), body)
            else:
                self.assertEqual((target / entry).read_bytes(), b"")
            self.assertEqual(len(port.calls), 1)
