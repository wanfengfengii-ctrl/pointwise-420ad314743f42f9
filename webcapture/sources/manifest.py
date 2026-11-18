from dataclasses import dataclass
from collections.abc import Iterable, Mapping


@dataclass(frozen=True)
class SourceEntry:
    entry_id: str
    url: str


def parse_manifest(entries: Iterable) -> tuple[SourceEntry, ...]:
    if isinstance(entries, (str, bytes, Mapping)):
        raise ValueError("manifest must be an ordered sequence of entries")
    result = []
    try:
        iterator = iter(entries)
    except TypeError as exc:
        raise ValueError("manifest is not iterable") from exc
    for item in iterator:
        if isinstance(item, SourceEntry):
            entry = item
        elif isinstance(item, Mapping) and set(item) == {"entry_id", "url"}:
            entry = SourceEntry(item["entry_id"], item["url"])
        else:
            raise ValueError("each entry needs exactly entry_id and url")
        if not isinstance(entry.entry_id, str) or not isinstance(entry.url, str):
            raise ValueError("entry_id and url must be strings")
        result.append(entry)
    return tuple(result)
