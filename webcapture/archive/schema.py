from contextlib import contextmanager
import sqlite3

SCHEMA_VERSION = 1


class IntegrityError(RuntimeError):
    pass


_TABLES = {
    "evidence": ("id", "capture_id", "request_url", "final_url", "status", "headers", "body", "digest"),
    "manifests": ("capture_id", "payload", "digest"),
    "publications": ("capture_id", "manifest_digest"),
    "batches": ("batch_id", "canonical", "state", "failure_entry", "failure_reason"),
    "batch_entries": ("batch_id", "ordinal", "entry_id", "url", "capture_id"),
}

_DDL = (
    "CREATE TABLE evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, capture_id TEXT NOT NULL, "
    "request_url TEXT NOT NULL, final_url TEXT NOT NULL, status INTEGER NOT NULL, "
    "headers TEXT NOT NULL, body BLOB NOT NULL, digest TEXT NOT NULL, "
    "UNIQUE(capture_id, request_url))",
    "CREATE TABLE manifests (capture_id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)",
    "CREATE TABLE publications (capture_id TEXT PRIMARY KEY, manifest_digest TEXT NOT NULL)",
    "CREATE TABLE batches (batch_id TEXT PRIMARY KEY, canonical BLOB NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('collecting','sealed','failed')), "
    "failure_entry TEXT, failure_reason TEXT)",
    "CREATE TABLE batch_entries (batch_id TEXT NOT NULL REFERENCES batches(batch_id), "
    "ordinal INTEGER NOT NULL CHECK(ordinal >= 0), entry_id TEXT NOT NULL, url TEXT NOT NULL, "
    "capture_id TEXT REFERENCES publications(capture_id), PRIMARY KEY(batch_id, ordinal), "
    "UNIQUE(batch_id, entry_id))",
)


class ArchiveStore:
    def __init__(self, path):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        try:
            self.initialize()
        except BaseException:
            self.connection.close()
            raise

    def initialize(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if not tables and version == 0:
            with self.transaction():
                for statement in _DDL:
                    self.connection.execute(statement)
                self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return
        if version != SCHEMA_VERSION or not set(_TABLES).issubset(tables):
            raise IntegrityError("incompatible archive schema")
        for (table, expected), statement in zip(_TABLES.items(), _DDL):
            columns = tuple(row[1] for row in self.connection.execute(f"PRAGMA table_info({table})"))
            definition = self.connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            if columns != expected or " ".join(definition.split()) != " ".join(statement.split()):
                raise IntegrityError(f"incompatible table: {table}")

    @contextmanager
    def transaction(self):
        if self.connection.in_transaction:
            raise IntegrityError("nested archive transactions are unsupported")
        self.connection.execute("BEGIN")
        try:
            yield self.connection
            self.connection.commit()
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
