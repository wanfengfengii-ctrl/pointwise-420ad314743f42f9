from dataclasses import dataclass
from enum import Enum
from webcapture.sources.responses import HttpResponse
from webcapture.html.encoding import DecodedHtml
from webcapture.html.references import ImageOccurrence
from webcapture.capture.resources import ImageResources


class CaptureError(RuntimeError):
    pass


class CandidateState(str, Enum):
    CREATED = "created"
    HTML_READY = "html_ready"
    IMAGES_PENDING = "images_pending"
    COMPLETE = "complete"
    REJECTED = "rejected"
    PUBLISHED = "published"


@dataclass
class PageCandidate:
    entry_url: str
    state: CandidateState = CandidateState.CREATED
    html: HttpResponse | None = None
    decoded: DecodedHtml | None = None
    occurrences: tuple[ImageOccurrence, ...] = ()
    resources: ImageResources | None = None
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
        self.resources = ImageResources(response)
        self.state = CandidateState.HTML_READY

    def set_occurrences(self, occurrences: tuple[ImageOccurrence, ...]) -> tuple[str, ...]:
        self._require(CandidateState.HTML_READY)
        requests = self.resources.plan(occurrences)
        self.occurrences = tuple(occurrences)
        self.state = CandidateState.IMAGES_PENDING
        return requests

    def mark_complete(self) -> None:
        self._require(CandidateState.IMAGES_PENDING)
        from webcapture.capture.completeness import check_candidate_complete
        check_candidate_complete(self)
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
