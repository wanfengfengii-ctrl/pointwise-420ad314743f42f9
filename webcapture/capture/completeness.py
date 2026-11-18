from webcapture.capture.jobs import CaptureError, CandidateState
from webcapture.html.addresses import resolve_image_address
from webcapture.html.document import parse_document
from webcapture.html.references import find_image_occurrences
from webcapture.sources.requests import build_request
from webcapture.sources.responses import validate_response


def check_candidate_complete(candidate) -> None:
    if candidate.state not in (CandidateState.IMAGES_PENDING, CandidateState.COMPLETE):
        raise CaptureError("candidate has not planned images")
    if candidate.html is None or candidate.decoded is None or candidate.resources is None:
        raise CaptureError("candidate has no HTML evidence")
    resources = candidate.resources
    if not resources.planned or resources.html != candidate.html:
        raise CaptureError("candidate resource context mismatch")
    parsed = find_image_occurrences(parse_document(candidate.decoded))
    if parsed != candidate.occurrences:
        raise CaptureError("candidate occurrences disagree with original HTML")
    if len(resources.bindings) != len(parsed):
        raise CaptureError("incomplete occurrence bindings")
    expected_requests = {}
    for ordinal, (occurrence, binding) in enumerate(zip(parsed, resources.bindings)):
        address = resolve_image_address(candidate.html.final_url, occurrence.source)
        if (occurrence.ordinal != ordinal or binding.occurrence != occurrence
                or binding.request_url != address.request_url
                or binding.fragment != address.fragment):
            raise CaptureError("invalid image binding")
        expected_requests.setdefault(address.request_url, None)
        if address.request_url not in resources.responses:
            raise CaptureError(f"missing image: {address.request_url}")
    if tuple(expected_requests) != resources.image_requests:
        raise CaptureError("image request order mismatch")
    if set(resources.responses) != set(expected_requests) | {candidate.entry_url}:
        raise CaptureError("unexpected or missing candidate response")
    for url, response in resources.responses.items():
        validate_response(build_request(url), response)
    if resources.responses[candidate.entry_url] != candidate.html:
        raise CaptureError("HTML resource was replaced")
