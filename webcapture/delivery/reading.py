from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from webcapture.archive.evidence import EvidenceStore, StoredEvidence
from webcapture.archive.manifests import CaptureManifest, ManifestStore
from webcapture.archive.publication import PublicationStore


@dataclass(frozen=True)
class CaptureReadView:
    capture_id: str
    manifest: CaptureManifest
    evidence: Mapping[int, StoredEvidence]

    @property
    def html(self) -> StoredEvidence:
        return self.evidence[self.manifest.html_evidence_id]

    @property
    def images(self) -> tuple[StoredEvidence, ...]:
        return tuple(self.evidence[identity] for _, identity in self.manifest.images)


def read_capture(archive, capture_id: str) -> CaptureReadView:
    with archive.transaction():
        PublicationStore(archive).require_published(capture_id)
        manifest = ManifestStore(archive).read(capture_id)
        identities = dict.fromkeys([manifest.html_evidence_id] +
                                   [identity for _, identity in manifest.images])
        evidence = EvidenceStore(archive)
        records = {identity: evidence.read(identity) for identity in identities}
    return CaptureReadView(capture_id, manifest, MappingProxyType(records))
