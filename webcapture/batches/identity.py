import hashlib
import json


def canonical_batch_bytes(sources) -> bytes:
    pairs = []
    for ordinal, source in enumerate(sources):
        if source.ordinal != ordinal:
            raise ValueError("source ordinals must be contiguous and ordered")
        pairs.append([source.entry_id, source.url])
    return json.dumps(pairs, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def compute_batch_id(sources) -> str:
    return hashlib.sha256(canonical_batch_bytes(sources)).hexdigest()
