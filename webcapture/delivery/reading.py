from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from webcapture.archive.evidence import EvidenceStore, StoredEvidence
from webcapture.archive.manifests import CaptureManifest, ManifestStore, is_style_payload
from webcapture.archive.publication import PublicationStore
from webcapture.archive.style_manifests import BINARY_ROLE, StyleManifest, StyleManifestStore
from webcapture.html.encoding import DecodedHtml

PAGE_KIND = "page"
STYLE_KIND = "styles"


@dataclass(frozen=True)
class CaptureReadView:
    """A read-only snapshot of one fixed capture.

    ``manifest`` is either a V1 :class:`CaptureManifest` or a
    :class:`StyleManifest`.  ``evidence`` holds every original response byte
    string referenced by that manifest.
    """

    capture_id: str
    manifest: CaptureManifest | StyleManifest
    evidence: Mapping[int, StoredEvidence]

    @property
    def kind(self) -> str:
        return STYLE_KIND if isinstance(self.manifest, StyleManifest) else PAGE_KIND

    @property
    def html(self) -> StoredEvidence:
        return self.evidence[self.manifest.html_evidence_id]

    @property
    def images(self) -> tuple[StoredEvidence, ...]:
        """Binary-role evidence in first-request order.

        For a V1 capture these are the direct images.  For a style capture
        they additionally include binary ``url(...)`` targets.
        """
        if isinstance(self.manifest, StyleManifest):
            return tuple(self.evidence[resource.evidence_id]
                         for resource in self.manifest.resources
                         if BINARY_ROLE in resource.roles)
        return tuple(self.evidence[identity] for _, identity in self.manifest.images)

    @property
    def style_manifest(self) -> StyleManifest | None:
        return self.manifest if isinstance(self.manifest, StyleManifest) else None


@dataclass(frozen=True)
class VerifiedCapture:
    """A verified capture.

    ``documents`` maps each interpreted document to its strictly decoded
    text; it is only populated for style captures.  ``responses`` maps the
    fixed request identity to the original response, likewise style-only.
    """

    view: CaptureReadView
    html: DecodedHtml
    documents: Mapping[str, DecodedHtml] | None = None
    responses: Mapping[str, object] | None = None

    @property
    def kind(self) -> str:
        return self.view.kind

    @property
    def style_manifest(self) -> StyleManifest | None:
        return self.view.style_manifest


def read_capture(archive, capture_id: str) -> CaptureReadView:
    with archive.transaction():
        PublicationStore(archive).require_published(capture_id)
        manifest_store = ManifestStore(archive)
        raw = manifest_store.verified_raw(capture_id)
        if is_style_payload(raw):
            manifest = StyleManifestStore(archive).read(capture_id)
            identities = {resource.evidence_id for resource in manifest.resources}
        else:
            manifest = manifest_store.read(capture_id)
            identities = dict.fromkeys([manifest.html_evidence_id] +
                                       [identity for _, identity in manifest.images])
        evidence = EvidenceStore(archive)
        records = {identity: evidence.read(identity) for identity in identities}
    return CaptureReadView(capture_id, manifest, MappingProxyType(records))
