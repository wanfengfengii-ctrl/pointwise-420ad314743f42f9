import unittest
from run import FixtureHttp, response_event
from webcapture.archive.schema import ArchiveStore
from webcapture.capture.service import capture_page
from webcapture.capture.jobs import CaptureError
from webcapture.delivery.verification import verify_capture


class TestPageCapture(unittest.TestCase):
    def setUp(self):
        self.archive = ArchiveStore(":memory:")
        self.addCleanup(self.archive.close)

    def test_redirect_image_base(self):
        port = FixtureHttp([
            response_event("https://e.test/old/page", b'<img src="pic.png#mark">',
                           final="https://e.test/current/day/page"),
            response_event("https://e.test/current/day/pic.png", b"correct")],
            alternatives=[response_event("https://e.test/old/pic.png", b"decoy")])
        identity = capture_page(self.archive, port, "https://e.test/old/page")
        view = verify_capture(self.archive, identity).view
        self.assertEqual(view.html.response.request_url, "https://e.test/old/page")
        self.assertEqual(view.html.response.final_url, "https://e.test/current/day/page")
        self.assertEqual(view.images[0].response.body, b"correct")
        self.assertEqual(view.manifest.references[0].fragment, "mark")
        self.assertEqual(port.calls, ["https://e.test/old/page", "https://e.test/current/day/pic.png"])
        port.assert_consumed()

    def test_distinct_requests_shared_final(self):
        port = FixtureHttp([
            response_event("https://e.test/page", b'<img src="/a"><img src="/b"><img src="/a">'),
            response_event("https://e.test/a", b"A", final="https://cdn.test/common"),
            response_event("https://e.test/b", b"B", final="https://cdn.test/common")])
        view = verify_capture(self.archive, capture_page(self.archive, port, "https://e.test/page")).view
        self.assertEqual([e.response.body for e in view.images], [b"A", b"B"])
        self.assertEqual([e.response.final_url for e in view.images], ["https://cdn.test/common"] * 2)
        self.assertNotEqual(view.images[0].evidence_id, view.images[1].evidence_id)
        self.assertEqual([r.request_url for r in view.manifest.references],
                         ["https://e.test/a", "https://e.test/b", "https://e.test/a"])
        self.assertEqual(len(port.calls), 3)

    def test_fragment_targets(self):
        port = FixtureHttp([response_event("https://e.test/page",
                                          b'<img src="/p#one"><img src="/p#two">'),
                            response_event("https://e.test/p", b"P")])
        view = verify_capture(self.archive, capture_page(self.archive, port, "https://e.test/page")).view
        self.assertEqual([r.fragment for r in view.manifest.references], ["one", "two"])
        self.assertEqual(len(view.images), 1)
        self.assertEqual(port.calls, ["https://e.test/page", "https://e.test/p"])

    def test_unsupported_url(self):
        for source in ("data:image/png,x", "file:///tmp/p", "", "javascript:x", "a b"):
            port = FixtureHttp([response_event("https://e.test/page",
                                              ('<img src="' + source + '">').encode())])
            with self.assertRaises(CaptureError):
                capture_page(self.archive, port, "https://e.test/page")
            self.assertEqual(port.calls, ["https://e.test/page"])
        self.assertEqual(self.archive.connection.execute("SELECT count(*) FROM publications").fetchone()[0], 0)

    def test_response_contract(self):
        from webcapture.sources.requests import build_request, normalize_request_url
        from webcapture.sources.responses import HttpResponse, ResponseError, validate_response
        request = build_request("https://e.test/%2f?a=2&a=1#part")
        self.assertEqual(request.url, "https://e.test/%2f?a=2&a=1")
        for url in ("https://", "https://u:p@e.test/x", "https://e.test:0/x", "https://e.test/a b"):
            with self.assertRaises(ValueError):
                normalize_request_url(url)
        for response in (HttpResponse("https://other.test/", request.url, 200, (), b""),
                         HttpResponse(request.url, request.url, 404, (), b""),
                         HttpResponse(request.url, request.url, 200, (), "text")):
            with self.assertRaises(ResponseError):
                validate_response(request, response)
