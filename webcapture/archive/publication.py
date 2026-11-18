import uuid
from webcapture.archive.schema import IntegrityError
from webcapture.archive.evidence import EvidenceStore
from webcapture.archive.manifests import CaptureManifest, ImageReferenceRecord, ManifestStore
from webcapture.capture.completeness import check_candidate_complete
from webcapture.capture.jobs import CandidateState


class PublicationStore:
    def __init__(self, archive):
        self.archive = archive

    def publish(self, candidate) -> str:
        if candidate.state != CandidateState.COMPLETE:
            raise IntegrityError("only complete candidates can be published")
        check_candidate_complete(candidate)
        capture_id = uuid.uuid4().hex
        with self.archive.transaction():
            evidence = EvidenceStore(self.archive)
            identities = {url: evidence.stage(capture_id, response)
                          for url, response in candidate.resources.responses.items()}
            references = tuple(ImageReferenceRecord(
                binding.occurrence.ordinal, binding.occurrence.start, binding.occurrence.end,
                binding.occurrence.source, binding.occurrence.token,
                binding.request_url, binding.fragment)
                for binding in candidate.resources.bindings)
            manifest = CaptureManifest(
                identities[candidate.entry_url],
                tuple((url, identities[url]) for url in candidate.resources.image_requests),
                references)
            digest = ManifestStore(self.archive).save(capture_id, manifest)
            self.archive.connection.execute(
                "INSERT INTO publications(capture_id,manifest_digest) VALUES(?,?)",
                (capture_id, digest))
        return capture_id

    def require_published(self, capture_id: str) -> None:
        if not self.is_published(capture_id):
            raise KeyError(capture_id)

    def is_published(self, capture_id: str) -> bool:
        return self.archive.connection.execute(
            "SELECT 1 FROM publications WHERE capture_id=?", (capture_id,)).fetchone() is not None
