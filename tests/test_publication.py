from pathlib import Path
import tempfile
import unittest
from run import FixtureHttp, response_event
from webcapture.archive.schema import ArchiveStore
from webcapture.capture.service import capture_page
from webcapture.capture.jobs import CaptureError, PageCandidate
from webcapture.archive.publication import PublicationStore
from webcapture.archive.evidence import EvidenceStore
from webcapture.sources.responses import HttpResponse
from webcapture.delivery.reading import read_capture
from webcapture.delivery.verification import verify_capture
from webcapture.delivery.html_export import export_capture


class TestPublication(unittest.TestCase):
    def setUp(self):
        self.archive = ArchiveStore(":memory:")
        self.addCleanup(self.archive.close)
        self.old = capture_page(self.archive, FixtureHttp([
            response_event("https://e.test/old", b"old evidence")]), "https://e.test/old")

    def counts(self):
        return tuple(self.archive.connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                     for table in ("evidence", "manifests", "publications"))

    def test_missing_image_preserves_previous(self):
        before = self.counts()
        port = FixtureHttp([response_event("https://e.test/new", b'<img src="missing">'),
                            response_event("https://e.test/missing", b"not found", status=404)])
        with self.assertRaises(CaptureError):
            capture_page(self.archive, port, "https://e.test/new")
        self.assertEqual(self.counts(), before)
        self.assertEqual(read_capture(self.archive, self.old).html.response.body, b"old evidence")
        with tempfile.TemporaryDirectory() as directory:
            entry = export_capture(self.archive, self.old, Path(directory) / "old")
            self.assertEqual((Path(directory) / "old" / entry).read_bytes(), b"old evidence")

    def test_publish_failure(self):
        before = self.counts()
        self.archive.connection.execute(
            "CREATE TRIGGER fail_manifest BEFORE INSERT ON manifests "
            "BEGIN SELECT RAISE(ABORT,'controlled manifest write failure'); END")
        with self.assertRaises(CaptureError):
            capture_page(self.archive, FixtureHttp([
                response_event("https://e.test/new", b'<img src="p">'),
                response_event("https://e.test/p", b"image")]), "https://e.test/new")
        self.assertEqual(self.counts(), before)
        self.assertFalse(self.archive.connection.in_transaction)
        self.assertEqual(verify_capture(self.archive, self.old).view.html.response.body, b"old evidence")
        self.archive.connection.execute("DROP TRIGGER fail_manifest")
        new = capture_page(self.archive, FixtureHttp([
            response_event("https://e.test/new", b"after failure")]), "https://e.test/new")
        self.assertNotEqual(new, self.old)
        self.assertEqual(self.counts(), tuple(n + 1 for n in before))

    def test_staging_and_visibility_boundaries(self):
        from webcapture.archive.schema import IntegrityError
        with self.assertRaises(IntegrityError):
            PublicationStore(self.archive).publish(PageCandidate("https://e.test/page"))
        with self.assertRaises(IntegrityError):
            EvidenceStore(self.archive).stage(
                "uncommitted", HttpResponse("https://e.test/page", "https://e.test/page", 200, (), b""))
        with self.assertRaises(KeyError):
            read_capture(self.archive, "uncommitted")
