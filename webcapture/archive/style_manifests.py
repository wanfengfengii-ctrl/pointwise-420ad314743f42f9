"""Fixed manifest for a style capture (HTML + CSS + binary closure).

A style capture records every request identity it used, the evidence that
proves each response, the roles that identity played and every supported
reference with its document, character span and resolved identity.  The
manifest is stored through the same ``manifests``/``publications`` rows as a
V1 capture, but its canonical payload carries ``kind: "styles"``.

Verification never trusts these reference records alone; it recomputes them
from the fixed response bytes.  The digest only proves the manifest was not
edited after publication.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
from webcapture.archive.manifests import is_style_payload
from webcapture.archive.schema import IntegrityError
from webcapture.capture.styles import (BINARY_ROLE, CSS_DOCUMENT_PREFIX, CSS_ROLE,
                                       HTML_DOCUMENT, HTML_ROLE, document_resource)
from webcapture.sources.requests import normalize_request_url

ROLE_ORDER = (HTML_ROLE, CSS_ROLE, BINARY_ROLE)

MANIFEST_KIND = "styles"

_KIND_ROLE = {
    "image": BINARY_ROLE,
    "url": BINARY_ROLE,
    "link": CSS_ROLE,
    "import": CSS_ROLE,
}

_KIND_CONTEXTS = {
    "image": ("attribute",),
    "link": ("attribute",),
    "url": ("style_attribute", "style_text", "css_text"),
    "import": ("style_attribute", "style_text", "css_text"),
}


@dataclass(frozen=True)
class StyleResourceRecord:
    request_url: str
    evidence_id: int
    roles: tuple[str, ...]


@dataclass(frozen=True)
class StyleReferenceRecord:
    document: str
    kind: str
    context: str
    ordinal: int
    start: int
    end: int
    token: str
    source: str
    request_url: str
    fragment: str


@dataclass(frozen=True)
class StyleManifest:
    html_evidence_id: int
    resources: tuple[StyleResourceRecord, ...]
    documents: tuple[str, ...]
    references: tuple[StyleReferenceRecord, ...]


def canonical_roles(roles) -> tuple[str, ...]:
    chosen = set(roles)
    return tuple(role for role in ROLE_ORDER if role in chosen)


def _check_identity(value, message: str) -> str:
    try:
        if not isinstance(value, str) or not value or normalize_request_url(value) != value:
            raise ValueError(message)
    except ValueError as exc:
        raise IntegrityError(message) from exc
    return value


def validate_style_manifest(manifest: StyleManifest) -> None:
    if not isinstance(manifest, StyleManifest):
        raise IntegrityError("invalid style manifest")
    if type(manifest.html_evidence_id) is not int or manifest.html_evidence_id <= 0:
        raise IntegrityError("invalid HTML evidence identity")
    if not manifest.resources:
        raise IntegrityError("style manifest has no resources")

    resources = {}
    evidence_ids = set()
    for ordinal, resource in enumerate(manifest.resources):
        if not isinstance(resource, StyleResourceRecord):
            raise IntegrityError("invalid style resource record")
        _check_identity(resource.request_url, "invalid resource request identity")
        if resource.request_url in resources:
            raise IntegrityError("duplicate resource request identity")
        if type(resource.evidence_id) is not int or resource.evidence_id <= 0:
            raise IntegrityError("invalid resource evidence identity")
        if resource.evidence_id in evidence_ids:
            raise IntegrityError("duplicate resource evidence identity")
        if not isinstance(resource.roles, tuple) or not resource.roles:
            raise IntegrityError("resource roles must be a nonempty tuple")
        if any(role not in ROLE_ORDER for role in resource.roles):
            raise IntegrityError("invalid resource roles")
        if resource.roles != canonical_roles(resource.roles):
            raise IntegrityError("resource roles are not canonical")
        if (HTML_ROLE in resource.roles) != (ordinal == 0):
            raise IntegrityError("only the first resource may be the HTML entry")
        if ordinal == 0 and resource.evidence_id != manifest.html_evidence_id:
            raise IntegrityError("HTML evidence identity mismatch")
        resources[resource.request_url] = resource
        evidence_ids.add(resource.evidence_id)

    if (not isinstance(manifest.documents, tuple) or not manifest.documents
            or manifest.documents[0] != HTML_DOCUMENT
            or manifest.documents.count(HTML_DOCUMENT) != 1):
        raise IntegrityError("invalid interpreted document order")
    interpreted = {}
    for position, document in enumerate(manifest.documents):
        if not isinstance(document, str):
            raise IntegrityError("invalid interpreted document")
        if document in interpreted:
            raise IntegrityError("duplicate interpreted document")
        if document == HTML_DOCUMENT:
            interpreted[document] = position
            continue
        request = document_resource(document)
        if request is None:
            raise IntegrityError("unknown interpreted document")
        if document != CSS_DOCUMENT_PREFIX + request:
            raise IntegrityError("noncanonical interpreted document identity")
        if request not in resources or CSS_ROLE not in resources[request].roles:
            raise IntegrityError("interpreted document is not a captured CSS resource")
        interpreted[document] = position
    for request, resource in resources.items():
        if CSS_ROLE in resource.roles and CSS_DOCUMENT_PREFIX + request not in interpreted:
            raise IntegrityError("CSS resource was never interpreted as a document")

    derived = {request: set() for request in resources}
    positions = {}
    ordinals = {}
    for reference in manifest.references:
        if not isinstance(reference, StyleReferenceRecord):
            raise IntegrityError("invalid style reference record")
        if reference.kind not in _KIND_ROLE:
            raise IntegrityError("unsupported reference kind")
        if reference.context not in _KIND_CONTEXTS[reference.kind]:
            raise IntegrityError("reference context does not fit its kind")
        for value in (reference.token, reference.source, reference.fragment):
            if not isinstance(value, str):
                raise IntegrityError("invalid reference strings")
        if not reference.token or not reference.source:
            raise IntegrityError("reference token and source must be nonempty")
        if reference.document not in interpreted:
            raise IntegrityError("reference document was not interpreted")
        if reference.document == HTML_DOCUMENT:
            if reference.context == "css_text":
                raise IntegrityError("HTML reference cannot use the CSS text context")
        elif reference.context != "css_text":
            raise IntegrityError("CSS document reference context mismatch")
        if reference.request_url not in resources:
            raise IntegrityError("dangling style reference")
        if type(reference.start) is not int or type(reference.end) is not int:
            raise IntegrityError("invalid reference position")
        if reference.start < 0 or reference.end <= reference.start:
            raise IntegrityError("invalid reference span")
        if reference.end - reference.start < len(reference.token):
            raise IntegrityError("reference token exceeds its span")
        previous = positions.get(reference.document)
        if previous is not None and reference.start < previous:
            raise IntegrityError("reference positions are not in document order")
        positions[reference.document] = reference.end
        expected_ordinal = ordinals.get(reference.document, 0)
        if reference.ordinal != expected_ordinal:
            raise IntegrityError("invalid reference ordinal")
        ordinals[reference.document] = expected_ordinal + 1
        derived[reference.request_url].add(_KIND_ROLE[reference.kind])

    for request, resource in resources.items():
        expected = set(derived[request])
        if HTML_ROLE in resource.roles:
            expected.add(HTML_ROLE)
        if not expected or set(resource.roles) != expected:
            raise IntegrityError("resource roles disagree with the recorded references")


def style_manifest_bytes(manifest: StyleManifest) -> bytes:
    value = asdict(manifest)
    value["kind"] = MANIFEST_KIND
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def parse_style_manifest(raw: bytes) -> StyleManifest:
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get("kind") != MANIFEST_KIND
            or set(value) != {"kind", "html_evidence_id", "resources", "documents",
                              "references"}):
        raise ValueError("not a style manifest")
    if (not isinstance(value["resources"], list) or not isinstance(value["references"], list)
            or not isinstance(value["documents"], list)):
        raise ValueError("invalid style manifest containers")
    resources = []
    for record in value["resources"]:
        if not isinstance(record, dict) or set(record) != {"request_url", "evidence_id", "roles"}:
            raise ValueError("invalid resource fields")
        roles = record["roles"]
        if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
            raise ValueError("invalid resource roles")
        resources.append(StyleResourceRecord(record["request_url"], record["evidence_id"],
                                             tuple(roles)))
    references = []
    expected_reference = {"document", "kind", "context", "ordinal", "start", "end",
                          "token", "source", "request_url", "fragment"}
    for record in value["references"]:
        if not isinstance(record, dict) or set(record) != expected_reference:
            raise ValueError("invalid reference fields")
        references.append(StyleReferenceRecord(**record))
    documents = tuple(value["documents"])
    if any(not isinstance(document, str) for document in documents):
        raise ValueError("invalid interpreted documents")
    return StyleManifest(value["html_evidence_id"], tuple(resources), documents,
                         tuple(references))


def is_style_manifest_payload(raw: bytes) -> bool:
    """True when fixed manifest bytes belong to a style capture."""
    return is_style_payload(raw)


class StyleManifestStore:
    def __init__(self, archive):
        self.archive = archive

    def save(self, capture_id: str, manifest: StyleManifest) -> str:
        if not self.archive.connection.in_transaction:
            raise IntegrityError("manifest staging requires a transaction")
        validate_style_manifest(manifest)
        payload = style_manifest_bytes(manifest)
        digest = hashlib.sha256(payload).hexdigest()
        self.archive.connection.execute(
            "INSERT INTO manifests(capture_id,payload,digest) VALUES(?,?,?)",
            (capture_id, payload.decode("utf-8"), digest))
        return digest

    def read(self, capture_id: str) -> StyleManifest:
        from webcapture.archive.manifests import ManifestStore
        raw = ManifestStore(self.archive).verified_raw(capture_id)
        if not is_style_manifest_payload(raw):
            raise IntegrityError("capture does not hold a style manifest")
        try:
            manifest = parse_style_manifest(raw)
            validate_style_manifest(manifest)
        except (ValueError, TypeError, KeyError) as exc:
            raise IntegrityError("invalid style manifest") from exc
        if style_manifest_bytes(manifest) != raw:
            raise IntegrityError("noncanonical style manifest")
        return manifest
