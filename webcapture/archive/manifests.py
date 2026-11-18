from dataclasses import asdict, dataclass
import hashlib
import json
from webcapture.archive.schema import IntegrityError
from webcapture.sources.requests import normalize_request_url


@dataclass(frozen=True)
class ImageReferenceRecord:
    ordinal: int
    start: int
    end: int
    source: str
    token: str
    request_url: str
    fragment: str


@dataclass(frozen=True)
class CaptureManifest:
    html_evidence_id: int
    images: tuple[tuple[str, int], ...]
    references: tuple[ImageReferenceRecord, ...]


def validate_manifest_structure(manifest: CaptureManifest) -> None:
    def evidence_id(value):
        if type(value) is not int or value <= 0:
            raise IntegrityError("invalid evidence identity")
    evidence_id(manifest.html_evidence_id)
    seen = set()
    for request, identity in manifest.images:
        if not isinstance(request, str) or normalize_request_url(request) != request or request in seen:
            raise IntegrityError("invalid or duplicate image request")
        evidence_id(identity)
        seen.add(request)
    last_end = 0
    encountered = {}
    for ordinal, reference in enumerate(manifest.references):
        if type(reference.ordinal) is not int or reference.ordinal != ordinal:
            raise IntegrityError("invalid occurrence order")
        if (type(reference.start) is not int or type(reference.end) is not int
                or reference.start < last_end or reference.end <= reference.start):
            raise IntegrityError("overlapping or invalid occurrence positions")
        if any(not isinstance(value, str) for value in
               (reference.source, reference.token, reference.request_url, reference.fragment)):
            raise IntegrityError("invalid reference strings")
        if not reference.source or reference.end - reference.start != len(reference.token):
            raise IntegrityError("invalid occurrence token")
        if reference.request_url not in seen:
            raise IntegrityError("dangling image request")
        encountered.setdefault(reference.request_url, None)
        last_end = reference.end
    if tuple(encountered) != tuple(request for request, _ in manifest.images):
        raise IntegrityError("image mapping must follow first occurrence order")


def manifest_bytes(manifest: CaptureManifest) -> bytes:
    return json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


class ManifestStore:
    def __init__(self, archive):
        self.archive = archive

    def save(self, capture_id: str, manifest: CaptureManifest) -> str:
        if not self.archive.connection.in_transaction:
            raise IntegrityError("manifest staging requires a transaction")
        validate_manifest_structure(manifest)
        payload = manifest_bytes(manifest)
        digest = hashlib.sha256(payload).hexdigest()
        self.archive.connection.execute(
            "INSERT INTO manifests(capture_id,payload,digest) VALUES(?,?,?)",
            (capture_id, payload.decode("utf-8"), digest))
        return digest

    def read(self, capture_id: str) -> CaptureManifest:
        row = self.archive.connection.execute(
            "SELECT payload,digest FROM manifests WHERE capture_id=?", (capture_id,)).fetchone()
        publication = self.archive.connection.execute(
            "SELECT manifest_digest FROM publications WHERE capture_id=?", (capture_id,)).fetchone()
        if row is None or publication is None:
            raise IntegrityError("missing fixed manifest")
        try:
            raw = row["payload"].encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            if digest != row["digest"] or digest != publication["manifest_digest"]:
                raise IntegrityError("manifest digest mismatch")
            value = json.loads(raw)
            if set(value) != {"html_evidence_id", "images", "references"}:
                raise ValueError("unknown manifest fields")
            images = tuple((pair[0], pair[1]) for pair in value["images"] if len(pair) == 2)
            if len(images) != len(value["images"]):
                raise ValueError("invalid image pair")
            manifest = CaptureManifest(value["html_evidence_id"], images,
                                       tuple(ImageReferenceRecord(**ref) for ref in value["references"]))
            validate_manifest_structure(manifest)
            if manifest_bytes(manifest) != raw:
                raise IntegrityError("noncanonical fixed manifest")
            return manifest
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            raise IntegrityError("invalid fixed manifest") from exc
