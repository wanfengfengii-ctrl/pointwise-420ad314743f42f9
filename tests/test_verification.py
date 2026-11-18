from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest
from run import FixtureHttp, response_event
from webcapture.archive.schema import ArchiveStore, IntegrityError
from webcapture.capture.service import capture_page
from webcapture.delivery.reading import read_capture
from webcapture.delivery.verification import verify_capture, verify_read_view
from webcapture.delivery.html_export import export_capture


class TestEvidenceVerification(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "archive.sqlite"
        self.archive = ArchiveStore(self.db)
        self.port = FixtureHttp([response_event("https://e.test/page", b'<img src="p">'),
                                 response_event("https://e.test/p", b"image")])
        self.capture_id = capture_page(self.archive, self.port, "https://e.test/page")
        self.port.enabled = False
        self.addCleanup(lambda: self.archive.close())

    def reopen(self):
        self.archive.close()
        self.archive = ArchiveStore(self.db)

    def test_corrupt_image(self):
        view = read_capture(self.archive, self.capture_id)
        image = view.images[0]
        self.archive.connection.execute("UPDATE evidence SET body=? WHERE id=?", (b"damaged", image.evidence_id))
        self.reopen()
        with self.assertRaises(IntegrityError):
            verify_capture(self.archive, self.capture_id)
        target = Path(self.temp.name) / "corrupt-export"
        with self.assertRaises(IntegrityError):
            export_capture(self.archive, self.capture_id, target)
        self.assertFalse(target.exists())
        self.assertEqual(self.port.calls, ["https://e.test/page", "https://e.test/p"])

    def test_clean_snapshot(self):
        first = verify_capture(self.archive, self.capture_id)
        self.reopen()
        again = verify_capture(self.archive, self.capture_id)
        self.assertEqual(first.view.manifest, again.view.manifest)
        self.assertEqual(dict(first.view.evidence), dict(again.view.evidence))
        # A verified read view owns copied bytes; later SQL changes cannot change those bytes.
        image = again.view.images[0]
        self.archive.connection.execute("UPDATE evidence SET body=? WHERE id=?", (b"changed", image.evidence_id))
        self.assertEqual(again.view.images[0].response.body, b"image")
        with self.assertRaises(IntegrityError):
            verify_capture(self.archive, self.capture_id)

    def test_missing_body(self):
        image = read_capture(self.archive, self.capture_id).images[0]
        self.archive.connection.execute("DELETE FROM evidence WHERE id=?", (image.evidence_id,))
        with self.assertRaises(IntegrityError):
            read_capture(self.archive, self.capture_id)
        with self.assertRaises(IntegrityError):
            verify_capture(self.archive, self.capture_id)
        with self.assertRaises(IntegrityError):
            export_capture(self.archive, self.capture_id, Path(self.temp.name) / "missing")

    def test_dangling_reference(self):
        view = read_capture(self.archive, self.capture_id)
        reference = view.manifest.references[0]
        for bad in (replace(reference, end=10000),
                    replace(reference, request_url="https://e.test/absent"),
                    replace(reference, source="different")):
            manifest = replace(view.manifest, references=(bad,))
            with self.assertRaises(IntegrityError):
                verify_read_view(replace(view, manifest=manifest))
        images = ((view.manifest.images[0][0], 999999),)
        with self.assertRaises(IntegrityError):
            verify_read_view(replace(view, manifest=replace(view.manifest, images=images)))
        image_id = view.manifest.images[0][1]
        evidence = dict(view.evidence)
        evidence[image_id] = replace(evidence[image_id], capture_id="another")
        with self.assertRaises(IntegrityError):
            verify_read_view(replace(view, evidence=MappingProxyType(evidence)))

    def test_manifest_tampering(self):
        self.archive.connection.execute("UPDATE manifests SET payload='{}' WHERE capture_id=?", (self.capture_id,))
        with self.assertRaises(IntegrityError):
            verify_capture(self.archive, self.capture_id)
