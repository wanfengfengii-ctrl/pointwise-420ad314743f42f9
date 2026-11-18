"""Candidate state for a style capture.

The style candidate owns the already-fetched response map and the interpreted
dependency graph.  ``check_style_candidate_complete`` re-interprets the graph
from those response bytes only, so a complete candidate is exactly one whose
recorded references and roles are derivable from its evidence.
"""

from dataclasses import dataclass
from webcapture.capture.jobs import CandidateState, CaptureError
from webcapture.capture.styles import StyleGraph
from webcapture.html.encoding import DecodedHtml, decode_html
from webcapture.sources.responses import HttpResponse


@dataclass
class StyleCandidate:
    entry_url: str
    state: CandidateState = CandidateState.CREATED
    html: HttpResponse | None = None
    decoded: DecodedHtml | None = None
    graph: StyleGraph | None = None
    rejection: str | None = None
    capture_id: str | None = None

    def _require(self, state):
        if self.state != state:
            raise CaptureError(f"candidate is {self.state.value}, expected {state.value}")

    def accept_html(self, response: HttpResponse, decoded: DecodedHtml) -> None:
        self._require(CandidateState.CREATED)
        if decoded.original != response.body or response.request_url != self.entry_url:
            raise CaptureError("HTML candidate identity mismatch")
        self.html = response
        self.decoded = decoded
        self.state = CandidateState.HTML_READY

    def set_graph(self, graph: StyleGraph) -> None:
        self._require(CandidateState.HTML_READY)
        if graph.entry_request_url != self.entry_url:
            raise CaptureError("dependency graph entry mismatch")
        if graph.responses.get(self.entry_url) != self.html:
            raise CaptureError("dependency graph HTML mismatch")
        self.graph = graph
        self.state = CandidateState.IMAGES_PENDING

    def mark_complete(self) -> None:
        self._require(CandidateState.IMAGES_PENDING)
        check_style_candidate_complete(self)
        self.state = CandidateState.COMPLETE

    def reject(self, reason: str) -> None:
        if self.state == CandidateState.PUBLISHED:
            raise CaptureError("published candidate cannot be rejected")
        if self.state == CandidateState.REJECTED:
            return
        self.rejection = str(reason)
        self.state = CandidateState.REJECTED

    def mark_published(self, capture_id: str) -> None:
        self._require(CandidateState.COMPLETE)
        if not isinstance(capture_id, str) or not capture_id:
            raise CaptureError("invalid publication identity")
        self.capture_id = capture_id
        self.state = CandidateState.PUBLISHED


def _response_fetcher(responses):
    def fetch(request_url):
        try:
            return responses[request_url]
        except KeyError as exc:
            raise CaptureError(f"missing dependency response: {request_url}") from exc

    return fetch


def recompute_graph(responses, entry_url: str) -> StyleGraph:
    return StyleGraph(_response_fetcher(responses)).run(entry_url)


def check_style_candidate_complete(candidate: StyleCandidate) -> None:
    if candidate.state not in (CandidateState.IMAGES_PENDING, CandidateState.COMPLETE):
        raise CaptureError("candidate has not planned style dependencies")
    if candidate.html is None or candidate.decoded is None or candidate.graph is None:
        raise CaptureError("candidate has no fixed HTML evidence")
    graph = candidate.graph
    if graph.responses.get(candidate.entry_url) != candidate.html:
        raise CaptureError("candidate HTML resource was replaced")
    if decode_html(candidate.html.body).text != candidate.decoded.text:
        raise CaptureError("candidate HTML does not decode to itself")
    again = recompute_graph(graph.responses, candidate.entry_url)
    if again.order != graph.order:
        raise CaptureError("style dependency request order mismatch")
    if again.documents != graph.documents:
        raise CaptureError("style dependency interpretation order mismatch")
    for request_url in graph.order:
        if again.roles_for(request_url) != graph.roles_for(request_url):
            raise CaptureError(f"style dependency roles mismatch: {request_url}")
    for document in graph.documents:
        if again.references.get(document) != graph.references.get(document):
            raise CaptureError(f"style reference closure mismatch: {document}")
    if any(not graph.roles_for(request_url) for request_url in graph.order):
        raise CaptureError("style dependency without a role")
