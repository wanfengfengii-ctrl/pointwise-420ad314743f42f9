from dataclasses import dataclass
from webcapture.batches.normalization import normalize_sources
from webcapture.batches.identity import canonical_batch_bytes, compute_batch_id
from webcapture.batches.catalog import BatchCatalog
from webcapture.capture.service import CaptureService
from webcapture.capture.jobs import CaptureError


@dataclass(frozen=True)
class BatchCollectionResult:
    batch_id: str
    capture_ids: tuple[str, ...]


def collect_batch(archive, http, entries) -> BatchCollectionResult:
    sources = normalize_sources(entries)
    batch_id = compute_batch_id(sources)
    catalog = BatchCatalog(archive)
    state = catalog.reserve(batch_id, sources, canonical_batch_bytes(sources))
    if state == "sealed":
        return BatchCollectionResult(batch_id, tuple(
            entry.capture_id for entry in catalog.read_catalog(batch_id)))
    if state == "failed":
        raise CaptureError(catalog.failure(batch_id))
    if state != "new":
        raise CaptureError("batch is already collecting; automatic continuation is unsupported")
    service = CaptureService(archive, http)
    current = None
    try:
        for source in sources:
            current = source.entry_id
            capture_id = service.capture_page(source.url)
            catalog.record_capture(batch_id, source.ordinal, source.entry_id, capture_id)
        current = None
        catalog.seal(batch_id)
    except Exception as exc:
        message = str(exc)
        catalog.fail(batch_id, current, message)
        if isinstance(exc, CaptureError):
            raise
        raise CaptureError(message) from exc
    return BatchCollectionResult(batch_id, tuple(
        entry.capture_id for entry in catalog.read_catalog(batch_id)))
