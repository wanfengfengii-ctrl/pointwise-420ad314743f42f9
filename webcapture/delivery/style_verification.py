"""Verification of a style capture from its fixed evidence only."""

from types import MappingProxyType
from webcapture.archive.evidence import body_digest
from webcapture.archive.schema import IntegrityError
from webcapture.archive.style_manifests import StyleManifest, validate_style_manifest
from webcapture.capture.jobs import CaptureError
from webcapture.capture.style_jobs import recompute_graph
from webcapture.capture.styles import HTML_DOCUMENT, document_resource
from webcapture.delivery.reading import CaptureReadView, VerifiedCapture
from webcapture.html.encoding import decode_css, decode_html
from webcapture.sources.requests import build_request
from webcapture.sources.responses import validate_response

REFERENCE_FIELDS = ("document", "kind", "context", "ordinal", "start", "end",
                    "token", "source", "request_url", "fragment")


def _check_evidence(view: CaptureReadView, manifest: StyleManifest) -> None:
    expected_ids = {resource.evidence_id for resource in manifest.resources}
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
    for resource in manifest.resources:
        response = view.evidence[resource.evidence_id].response
        if response.request_url != resource.request_url:
            raise IntegrityError("resource request identity mismatch")


def verify_style_read_view(view: CaptureReadView) -> VerifiedCapture:
    try:
        manifest = view.manifest
        if not isinstance(manifest, StyleManifest):
            raise IntegrityError("not a style capture")
        validate_style_manifest(manifest)
        _check_evidence(view, manifest)
        responses = {resource.request_url: view.evidence[resource.evidence_id].response
                     for resource in manifest.resources}
        entry = manifest.resources[0].request_url
        graph = recompute_graph(responses, entry)
        if tuple(graph.order) != tuple(resource.request_url for resource in manifest.resources):
            raise IntegrityError("resource request order differs from fixed evidence")
        if tuple(graph.documents) != manifest.documents:
            raise IntegrityError("interpreted document order mismatch")
        for resource in manifest.resources:
            if tuple(resource.roles) != graph.roles_for(resource.request_url):
                raise IntegrityError("resource roles disagree with fixed evidence")
        recomputed = tuple(record for document in graph.documents
                           for record in graph.references[document])
        if len(recomputed) != len(manifest.references):
            raise IntegrityError("reference closure size mismatch")
        for expected, actual in zip(recomputed, manifest.references):
            for name in REFERENCE_FIELDS:
                if expected[name] != getattr(actual, name):
                    raise IntegrityError(f"reference mismatch: {name}")
        html = decode_html(responses[entry].body)
        documents = {}
        for document in graph.documents:
            source = entry if document == HTML_DOCUMENT else document_resource(document)
            body = responses[source].body
            documents[document] = decode_html(body) if document == HTML_DOCUMENT else decode_css(body)
        return VerifiedCapture(view, html, MappingProxyType(documents),
                               MappingProxyType(responses))
    except IntegrityError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, CaptureError) as exc:
        raise IntegrityError("style capture verification failed") from exc
