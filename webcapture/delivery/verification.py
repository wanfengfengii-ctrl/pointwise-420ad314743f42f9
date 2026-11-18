from webcapture.archive.schema import IntegrityError
from webcapture.archive.evidence import body_digest
from webcapture.archive.manifests import validate_manifest_structure
from webcapture.delivery.reading import (CaptureReadView, VerifiedCapture, read_capture,
                                         STYLE_KIND)
from webcapture.html.encoding import decode_html
from webcapture.html.document import parse_document
from webcapture.html.references import find_image_occurrences
from webcapture.html.addresses import resolve_image_address
from webcapture.sources.requests import build_request
from webcapture.sources.responses import validate_response


def verify_read_view(view: CaptureReadView) -> VerifiedCapture:
    if view.kind == STYLE_KIND:
        from webcapture.delivery.style_verification import verify_style_read_view
        return verify_style_read_view(view)
    try:
        manifest = view.manifest
        validate_manifest_structure(manifest)
        expected_ids = {manifest.html_evidence_id} | {identity for _, identity in manifest.images}
        if set(view.evidence) != expected_ids:
            raise IntegrityError("snapshot evidence set mismatch")
        requests = {}
        for identity, evidence in view.evidence.items():
            if evidence.evidence_id != identity or evidence.capture_id != view.capture_id:
                raise IntegrityError("evidence belongs to another capture")
            response = evidence.response
            validate_response(build_request(response.request_url), response)
            if body_digest(response.body) != evidence.digest:
                raise IntegrityError(f"body digest mismatch: {identity}")
            if response.request_url in requests:
                raise IntegrityError("duplicate request evidence")
            requests[response.request_url] = identity
        root = view.html.response
        decoded = decode_html(root.body)
        occurrences = find_image_occurrences(parse_document(decoded))
        if len(occurrences) != len(manifest.references):
            raise IntegrityError("HTML occurrence count mismatch")
        images = dict(manifest.images)
        for request, identity in manifest.images:
            if view.evidence[identity].response.request_url != request:
                raise IntegrityError("image request identity mismatch")
        for occurrence, reference in zip(occurrences, manifest.references):
            if (occurrence.ordinal, occurrence.start, occurrence.end, occurrence.source, occurrence.token) != (
                    reference.ordinal, reference.start, reference.end, reference.source, reference.token):
                raise IntegrityError("original HTML occurrence mismatch")
            address = resolve_image_address(root.final_url, occurrence.source)
            if (address.request_url, address.fragment) != (reference.request_url, reference.fragment):
                raise IntegrityError("image address context mismatch")
            if reference.end > len(decoded.text) or images[reference.request_url] not in view.evidence:
                raise IntegrityError("dangling reference")
        return VerifiedCapture(view, decoded)
    except IntegrityError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise IntegrityError("capture verification failed") from exc


def verify_capture(archive, capture_id: str) -> VerifiedCapture:
    return verify_read_view(read_capture(archive, capture_id))
