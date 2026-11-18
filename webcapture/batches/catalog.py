from dataclasses import dataclass
from html import escape
from webcapture.archive.schema import IntegrityError
from webcapture.archive.publication import PublicationStore
from webcapture.batches.identity import canonical_batch_bytes
from webcapture.batches.normalization import NormalizedSource
from webcapture.delivery.paths import ensure_relative_output


@dataclass(frozen=True)
class CatalogEntry:
    ordinal: int
    entry_id: str
    url: str
    capture_id: str


class BatchCatalog:
    def __init__(self, archive):
        self.archive = archive

    def _batch(self, batch_id):
        row = self.archive.connection.execute(
            "SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return row

    def _entries(self, batch_id):
        return tuple(self.archive.connection.execute(
            "SELECT * FROM batch_entries WHERE batch_id=? ORDER BY ordinal", (batch_id,)))

    def reserve(self, batch_id: str, sources, canonical: bytes) -> str:
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("batch identity required")
        sources = tuple(sources)
        if canonical_batch_bytes(sources) != canonical:
            raise ValueError("canonical content disagrees with sources")
        with self.archive.transaction():
            existing = self.archive.connection.execute(
                "SELECT canonical,state FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if existing is not None:
                if existing["canonical"] != canonical:
                    raise ValueError("batch identity conflict")
                self._validate_rows(batch_id, canonical)
                return existing["state"]
            self.archive.connection.execute(
                "INSERT INTO batches(batch_id,canonical,state) VALUES(?,?,'collecting')",
                (batch_id, canonical))
            for source in sources:
                self.archive.connection.execute(
                    "INSERT INTO batch_entries(batch_id,ordinal,entry_id,url) VALUES(?,?,?,?)",
                    (batch_id, source.ordinal, source.entry_id, source.url))
        return "new"

    def _validate_rows(self, batch_id, canonical):
        rows = self._entries(batch_id)
        sources = tuple(NormalizedSource(row["ordinal"], row["entry_id"], row["url"]) for row in rows)
        try:
            if canonical_batch_bytes(sources) != canonical:
                raise IntegrityError("batch entries disagree with canonical content")
        except ValueError as exc:
            raise IntegrityError("invalid stored batch order") from exc
        return rows

    def failure(self, batch_id) -> str:
        row = self._batch(batch_id)
        if row["state"] != "failed" or not isinstance(row["failure_reason"], str):
            raise IntegrityError("batch has no recorded failure")
        return row["failure_reason"]

    def record_capture(self, batch_id: str, ordinal: int, entry_id: str, capture_id: str) -> None:
        with self.archive.transaction():
            batch = self._batch(batch_id)
            if batch["state"] != "collecting":
                raise ValueError("batch is not collecting")
            rows = self._validate_rows(batch_id, batch["canonical"])
            if type(ordinal) is not int or not 0 <= ordinal < len(rows):
                raise ValueError("invalid source ordinal")
            row = rows[ordinal]
            if row["entry_id"] != entry_id or row["capture_id"] is not None:
                raise ValueError("source binding already set or mismatched")
            if any(previous["capture_id"] is None for previous in rows[:ordinal]):
                raise ValueError("source bindings must follow input order")
            PublicationStore(self.archive).require_published(capture_id)
            self.archive.connection.execute(
                "UPDATE batch_entries SET capture_id=? WHERE batch_id=? AND ordinal=?",
                (capture_id, batch_id, ordinal))

    def seal(self, batch_id: str) -> None:
        with self.archive.transaction():
            batch = self._batch(batch_id)
            if batch["state"] != "collecting":
                raise ValueError("only collecting batches may seal")
            rows = self._validate_rows(batch_id, batch["canonical"])
            for row in rows:
                if row["capture_id"] is None:
                    raise ValueError("batch is incomplete")
                PublicationStore(self.archive).require_published(row["capture_id"])
            self.archive.connection.execute("UPDATE batches SET state='sealed' WHERE batch_id=?", (batch_id,))

    def fail(self, batch_id: str, entry_id: str | None, reason: str) -> None:
        with self.archive.transaction():
            batch = self._batch(batch_id)
            if batch["state"] != "collecting":
                raise ValueError("only collecting batches may fail")
            if entry_id is not None:
                row = self.archive.connection.execute(
                    "SELECT capture_id FROM batch_entries WHERE batch_id=? AND entry_id=?",
                    (batch_id, entry_id)).fetchone()
                if row is None or row["capture_id"] is not None:
                    raise ValueError("failure must name an unbound entry")
            self.archive.connection.execute(
                "UPDATE batches SET state='failed',failure_entry=?,failure_reason=? WHERE batch_id=?",
                (entry_id, str(reason), batch_id))

    def read_catalog(self, batch_id: str) -> tuple[CatalogEntry, ...]:
        with self.archive.transaction():
            batch = self._batch(batch_id)
            if batch["state"] != "sealed":
                raise ValueError("only sealed batches have a delivery catalog")
            rows = self._validate_rows(batch_id, batch["canonical"])
            result = []
            for row in rows:
                if row["capture_id"] is None:
                    raise IntegrityError("sealed batch has an unbound source")
                try:
                    PublicationStore(self.archive).require_published(row["capture_id"])
                except KeyError as exc:
                    raise IntegrityError("batch references missing publication") from exc
                result.append(CatalogEntry(row["ordinal"], row["entry_id"], row["url"], row["capture_id"]))
            return tuple(result)


def render_delivery_catalog(entries, entrypoints) -> str:
    entries = tuple(entries)
    if set(entrypoints) != {entry.entry_id for entry in entries}:
        raise ValueError("entrypoint mapping must cover exactly the catalog")
    lines = ["<!doctype html><meta charset=\"utf-8\"><title>Research delivery</title><ul>"]
    for ordinal, entry in enumerate(entries):
        if entry.ordinal != ordinal:
            raise ValueError("catalog order is not contiguous")
        capture_id, path = entrypoints[entry.entry_id]
        if capture_id != entry.capture_id:
            raise ValueError("export belongs to another capture")
        ensure_relative_output(path)
        lines.append('<li><a href="' + escape(path, quote=True) + '">' +
                     escape(entry.entry_id) + '</a> <code>' + escape(capture_id) + '</code></li>')
    lines.append("</ul>")
    return "\n".join(lines)
