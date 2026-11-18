from dataclasses import dataclass
from webcapture.sources.manifest import parse_manifest
from webcapture.sources.requests import normalize_request_url


@dataclass(frozen=True)
class NormalizedSource:
    ordinal: int
    entry_id: str
    url: str


def normalize_sources(entries) -> tuple[NormalizedSource, ...]:
    parsed = parse_manifest(entries)
    seen = set()
    result = []
    for ordinal, entry in enumerate(parsed):
        entry_id = entry.entry_id.strip()
        if not entry_id or entry_id in seen:
            raise ValueError("entry_id must be nonempty and unique after normalization")
        url = normalize_request_url(entry.url.strip())
        seen.add(entry_id)
        result.append(NormalizedSource(ordinal, entry_id, url))
    return tuple(result)
