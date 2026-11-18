from webcapture.sources.requests import normalize_request_url
from webcapture.sources.transport import fetch_response
from webcapture.html.encoding import decode_html
from webcapture.html.document import parse_document
from webcapture.html.references import find_image_occurrences
from webcapture.capture.jobs import CaptureError, PageCandidate
from webcapture.capture.styles import StyleGraph
from webcapture.capture.style_jobs import StyleCandidate
from webcapture.archive.publication import PublicationStore
from webcapture.archive.style_publication import StylePublicationStore


class CaptureService:
    def __init__(self, archive, http):
        self.archive = archive
        self.http = http

    def capture_page(self, entry_url: str) -> str:
        try:
            request_url = normalize_request_url(entry_url)
        except ValueError as exc:
            raise CaptureError(f"capture rejected: {exc}") from exc
        candidate = PageCandidate(request_url)
        try:
            response = fetch_response(self.http, request_url)
            candidate.accept_html(response, decode_html(response.body))
            occurrences = find_image_occurrences(parse_document(candidate.decoded))
            for url in candidate.set_occurrences(occurrences):
                candidate.resources.record(fetch_response(self.http, url))
            candidate.mark_complete()
            capture_id = PublicationStore(self.archive).publish(candidate)
            candidate.mark_published(capture_id)
            return capture_id
        except Exception as exc:
            candidate.reject(str(exc))
            if isinstance(exc, CaptureError):
                raise
            raise CaptureError(f"capture rejected: {exc}") from exc

    def capture_styles(self, entry_url: str) -> str:
        try:
            request_url = normalize_request_url(entry_url)
        except ValueError as exc:
            raise CaptureError(f"capture rejected: {exc}") from exc
        candidate = StyleCandidate(request_url)
        try:
            graph = StyleGraph(lambda url: fetch_response(self.http, url)).run(request_url)
            candidate.accept_html(graph.responses[request_url], decode_html(
                graph.responses[request_url].body))
            candidate.set_graph(graph)
            candidate.mark_complete()
            capture_id = StylePublicationStore(self.archive).publish(candidate)
            candidate.mark_published(capture_id)
            return capture_id
        except Exception as exc:
            candidate.reject(str(exc))
            if isinstance(exc, CaptureError):
                raise
            raise CaptureError(f"capture rejected: {exc}") from exc


def capture_page(archive, http, entry_url: str) -> str:
    return CaptureService(archive, http).capture_page(entry_url)


def capture_styles(archive, http, entry_url: str) -> str:
    return CaptureService(archive, http).capture_styles(entry_url)
