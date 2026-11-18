import unittest
from run import FixtureHttp, response_event
from webcapture.archive.schema import ArchiveStore
from webcapture.archive.evidence import body_digest
from webcapture.capture.service import capture_page
from webcapture.capture.jobs import CaptureError, PageCandidate, CandidateState
from webcapture.html.encoding import decode_html
from webcapture.html.document import parse_document
from webcapture.html.references import find_image_occurrences
from webcapture.sources.responses import HttpResponse
from webcapture.delivery.verification import verify_capture
from webcapture.delivery.paths import plan_export_paths
from webcapture.delivery.html_export import rewrite_direct_images


class TestPageCapture(unittest.TestCase):
    def setUp(self):
        self.archive = ArchiveStore(":memory:")
        self.addCleanup(self.archive.close)

    def test_duplicate_image_occurrences(self):
        body = b'<img src="p"><img src=\'p\'><img src="empty">'
        port = FixtureHttp([response_event("https://e.test/page", body),
                            response_event("https://e.test/p", b"picture"),
                            response_event("https://e.test/empty", b"")])
        verified = verify_capture(self.archive, capture_page(self.archive, port, "https://e.test/page"))
        view = verified.view
        self.assertEqual(len(view.manifest.references), 3)
        self.assertEqual(len(view.images), 2)
        self.assertEqual([image.response.body for image in view.images], [b"picture", b""])
        self.assertEqual(view.images[1].digest, body_digest(b""))
        self.assertNotEqual(view.manifest.references[0].start, view.manifest.references[1].start)
        self.assertEqual(port.calls, ["https://e.test/page", "https://e.test/p", "https://e.test/empty"])
        derived = rewrite_direct_images(verified, plan_export_paths(verified)).decode()
        self.assertEqual(derived.count('src="image-0000.bin"'), 2)
        self.assertIn('src="image-0001.bin"', derived)

    def test_empty_body(self):
        port = FixtureHttp([response_event("https://e.test/page", b'<img src="empty">'),
                            response_event("https://e.test/empty", b"")])
        view = verify_capture(self.archive, capture_page(self.archive, port, "https://e.test/page")).view
        self.assertEqual(view.images[0].response.body, b"")
        self.assertEqual(view.images[0].digest,
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertEqual(len(view.manifest.references), 1)

    def test_no_images(self):
        for body in (b"", b"<p>plain</p>"):
            port = FixtureHttp([response_event("https://e.test/page", body)])
            view = verify_capture(self.archive, capture_page(self.archive, port, "https://e.test/page")).view
            self.assertEqual(view.html.response.body, body)
            self.assertEqual(view.images, ())
            self.assertEqual(view.manifest.references, ())
            port.assert_consumed()

    def test_root_reuse(self):
        body = b'<img src="/page#self">'
        port = FixtureHttp([response_event("https://e.test/page", body)])
        verified = verify_capture(self.archive, capture_page(self.archive, port, "https://e.test/page"))
        self.assertEqual(port.calls, ["https://e.test/page"])
        self.assertEqual(verified.view.manifest.html_evidence_id, verified.view.manifest.images[0][1])
        self.assertEqual(verified.view.images[0].response.body, body)
        self.assertIn(b'image-0000.bin#self', rewrite_direct_images(verified, plan_export_paths(verified)))

    def test_candidate_state_and_missing_resource(self):
        response = HttpResponse("https://e.test/page", "https://e.test/page", 200, (), b'<img src="p">')
        candidate = PageCandidate(response.request_url)
        with self.assertRaises(CaptureError):
            candidate.mark_complete()
        candidate.accept_html(response, decode_html(response.body))
        candidate.set_occurrences(find_image_occurrences(parse_document(candidate.decoded)))
        with self.assertRaises(CaptureError):
            candidate.mark_complete()
        candidate.resources.record(HttpResponse("https://e.test/p", "https://e.test/p", 200, (), b""))
        candidate.mark_complete()
        self.assertEqual(candidate.state, CandidateState.COMPLETE)
        candidate.mark_published("fixed")
        with self.assertRaises(CaptureError):
            candidate.reject("too late")
