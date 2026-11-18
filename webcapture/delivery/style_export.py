"""Offline export of style captures (dispatched from ``export_capture``)."""

from pathlib import Path
from webcapture.archive.schema import IntegrityError
from webcapture.delivery.html_export import ExportCandidate, write_file_exclusive
from webcapture.delivery.style_paths import plan_style_export_paths, rewrite_style_document
from webcapture.delivery.verification import verify_capture


def _style_derived(verified, plan) -> dict:
    manifest = verified.style_manifest
    by_document = {}
    for reference in manifest.references:
        by_document.setdefault(reference.document, []).append(reference)
    return {document: rewrite_style_document(
        plan, document, verified.documents[document].text, by_document.get(document, ()))
        .encode("utf-8")
        for document in manifest.documents}


def export_style_capture(archive, capture_id: str, target, *, writer=write_file_exclusive) -> str:
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise FileExistsError(str(target))
    verified = verify_capture(archive, capture_id)
    if verified.style_manifest is None:
        raise IntegrityError("capture does not hold a style manifest")
    plan = plan_style_export_paths(verified)
    manifest = verified.style_manifest
    derived = _style_derived(verified, plan)
    candidate = ExportCandidate(target)
    try:
        candidate.acquire()
        for resource in manifest.resources:
            if "binary" in resource.roles:
                candidate.write(plan.binary_path(resource.request_url),
                                verified.responses[resource.request_url].body, writer)
        for document in manifest.documents:
            if document != "html":
                candidate.write(plan.document_path(document), derived[document], writer)
        candidate.write(plan.entry, derived["html"], writer)
        candidate.deliver()
        return plan.entry
    except BaseException:
        candidate.abort()
        raise
