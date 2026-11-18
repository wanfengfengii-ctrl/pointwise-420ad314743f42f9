from dataclasses import dataclass
import hashlib
import json
from webcapture.archive.schema import IntegrityError
from webcapture.sources.requests import build_request
from webcapture.sources.responses import HttpResponse, validate_response


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class StoredEvidence:
    evidence_id: int
    capture_id: str
    response: HttpResponse
    digest: str


class EvidenceStore:
    def __init__(self, archive):
        self.archive = archive

    def stage(self, capture_id: str, response: HttpResponse) -> int:
        if not self.archive.connection.in_transaction:
            raise IntegrityError("evidence staging requires a transaction")
        validate_response(build_request(response.request_url), response)
        cursor = self.archive.connection.execute(
            "INSERT INTO evidence(capture_id,request_url,final_url,status,headers,body,digest) "
            "VALUES(?,?,?,?,?,?,?)",
            (capture_id, response.request_url, response.final_url, response.status,
             json.dumps(response.headers, ensure_ascii=False, separators=(",", ":")),
             response.body, body_digest(response.body)))
        return cursor.lastrowid

    def read(self, evidence_id: int) -> StoredEvidence:
        row = self.archive.connection.execute(
            "SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if row is None:
            raise IntegrityError(f"missing evidence: {evidence_id}")
        try:
            headers = json.loads(row["headers"])
            if not isinstance(headers, list) or any(
                    not isinstance(pair, list) or len(pair) != 2 for pair in headers):
                raise ValueError("invalid stored headers")
            response = HttpResponse(row["request_url"], row["final_url"], row["status"],
                                    tuple(tuple(pair) for pair in headers), row["body"])
            validate_response(build_request(response.request_url), response)
            if not isinstance(row["digest"], str) or len(row["digest"]) != 64:
                raise ValueError("invalid stored digest")
            return StoredEvidence(row["id"], row["capture_id"], response, row["digest"])
        except (ValueError, TypeError, KeyError) as exc:
            raise IntegrityError("invalid stored response evidence") from exc
