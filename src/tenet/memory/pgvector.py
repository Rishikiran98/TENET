"""Postgres + pgvector context store (build step 8).

The context store is still a disposable projection of the log (D2). Postgres is
its query engine, not a new source of truth: every row can be rebuilt by
replaying ``memory.raw.appended`` events, and ``MemoryCore`` rebuilds on start.

Security contract (§4.4): the namespace filter and the similarity ranking run in
one SQL statement, with the filter in its ``WHERE`` clause. Rows outside the
granted namespaces never enter the candidate set. The retriever decides which
namespaces are granted before this store is called.

Deliberate v1 choices, each a semantic guarantee rather than a tuning knob:

* **Exact search, no ANN index.** An HNSW/IVFFlat index combined with a
  namespace filter can silently return fewer results than exist. Exact search
  keeps results equal to the in-memory retriever. An index is a later,
  measured change.
* **Faithful records.** pgvector stores single-precision floats, so the exact
  embedding is also kept as ``double precision[]``. ``get``/``list`` return the
  record exactly as upserted; only ranking scores use single precision and may
  differ from the in-memory scores by about 1e-7.
* **Zero vectors score 0.** pgvector's cosine distance is NaN for a zero
  vector; this store maps it to similarity 0, matching the in-memory retriever.
* **Atomic rebuilds.** ``empty()`` returns a staging store on its own
  connection and transaction. It replaces every row and becomes visible to
  readers only on ``publish()``; ``discard()`` rolls it back. One table holds
  the projection of exactly one log.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from .models import ContextRecord, RetrievalResult

_TABLE = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_COLUMNS = ("context_id, raw_id, namespace, text, embedding_exact, "
            "contextualizer_version, raw_content_hash, source")


def _vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _record(row) -> ContextRecord:
    context_id, raw_id, namespace, text, exact, version, raw_hash, source = row[:8]
    return ContextRecord(context_id=context_id, raw_id=raw_id, namespace=namespace,
                         text=text, embedding=tuple(exact),
                         contextualizer_version=version, raw_content_hash=raw_hash,
                         source=source)


class PgVectorContextStore:
    """A ``ContextStore`` backed by one Postgres table with a pgvector column.

    ``dsn`` is a libpq connection string. ``dim`` must equal the embedder's
    dimension; a mismatched embedding is rejected by the column type.
    Requires the ``postgres`` extra and the ``vector`` extension (created here
    if the role may create it).
    """

    def __init__(self, dsn: str, *, dim: int, table: str = "tenet_context",
                 _staging: bool = False) -> None:
        import psycopg
        from psycopg import sql

        if type(dim) is not int or dim <= 0:
            raise ValueError("dim must be a positive integer")
        if not isinstance(table, str) or _TABLE.fullmatch(table) is None:
            raise ValueError("table must be a lowercase SQL identifier")
        self._dsn, self.dim, self.table = dsn, dim, table
        self._t = sql.Identifier(table)
        self._sql = sql
        self._staging = _staging
        self._conn = psycopg.connect(dsn, autocommit=not _staging)
        if not _staging:
            self._create_schema()

    # -- schema ----------------------------------------------------------
    def _create_schema(self) -> None:
        sql = self._sql
        with self._conn.transaction():
            self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            self._conn.execute(sql.SQL(
                "CREATE TABLE IF NOT EXISTS {t} ("
                " context_id text PRIMARY KEY,"
                " raw_id text NOT NULL,"
                " namespace text NOT NULL,"
                " text text NOT NULL,"
                " embedding_exact double precision[] NOT NULL,"
                " embedding vector({dim}),"
                " contextualizer_version text NOT NULL,"
                " raw_content_hash text NOT NULL,"
                " source text NOT NULL)"
            ).format(t=self._t, dim=sql.Literal(self.dim)))
            self._conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {i} ON {t} (namespace)")
                               .format(i=sql.Identifier(f"{self.table}_namespace"), t=self._t))
            row = self._conn.execute(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = %s::regclass AND attname = 'embedding'",
                (self.table,)).fetchone()
        if row is None or row[0] != f"vector({self.dim})":
            raise ValueError(f"table {self.table!r} holds {row and row[0]}, not vector({self.dim})")

    # -- ContextStore protocol ---------------------------------------------
    def upsert(self, record: ContextRecord) -> None:
        embedding = record.embedding
        if embedding and len(embedding) != self.dim:
            raise ValueError(f"embedding has {len(embedding)} dimensions, store expects {self.dim}")
        self._conn.execute(self._sql.SQL(
            "INSERT INTO {t} (" + _COLUMNS + ", embedding)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)"
            " ON CONFLICT (context_id) DO UPDATE SET"
            " raw_id = EXCLUDED.raw_id, namespace = EXCLUDED.namespace,"
            " text = EXCLUDED.text, embedding_exact = EXCLUDED.embedding_exact,"
            " embedding = EXCLUDED.embedding,"
            " contextualizer_version = EXCLUDED.contextualizer_version,"
            " raw_content_hash = EXCLUDED.raw_content_hash, source = EXCLUDED.source"
        ).format(t=self._t), (
            record.context_id, record.raw_id, record.namespace, record.text,
            list(embedding), record.contextualizer_version, record.raw_content_hash,
            record.source, _vector_literal(embedding) if embedding else None))

    def get(self, context_id: str) -> ContextRecord | None:
        row = self._conn.execute(self._sql.SQL(
            "SELECT " + _COLUMNS + " FROM {t} WHERE context_id = %s"
        ).format(t=self._t), (context_id,)).fetchone()
        return None if row is None else _record(row)

    def list(self, namespace: str | None = None) -> list[ContextRecord]:
        query = "SELECT " + _COLUMNS + " FROM {t}"
        params: tuple = ()
        if namespace is not None:
            query += " WHERE namespace = %s"
            params = (namespace,)
        rows = self._conn.execute(self._sql.SQL(query + " ORDER BY context_id")
                                  .format(t=self._t), params).fetchall()
        return [_record(r) for r in rows]

    def clear(self) -> None:
        self._conn.execute(self._sql.SQL("DELETE FROM {t}").format(t=self._t))

    # -- ranking pushed into SQL -------------------------------------------
    def search(self, query_vec: Sequence[float], namespaces: Sequence[str],
               limit: int) -> list[RetrievalResult]:
        """Rank within ``namespaces`` only. Callers authorize namespaces first."""
        if not namespaces or limit == 0:
            return []
        if len(query_vec) != self.dim or not all(math.isfinite(v) for v in query_vec):
            raise ValueError("query vector does not match the store dimension")
        rows = self._conn.execute(self._sql.SQL(
            "SELECT " + _COLUMNS + ", CASE WHEN d = 'NaN' THEN 0 ELSE 1 - d END AS score"
            " FROM (SELECT *, embedding <=> %s::vector AS d FROM {t}"
            "       WHERE namespace = ANY(%s) AND embedding IS NOT NULL) candidates"
            " ORDER BY score DESC, context_id LIMIT %s"
        ).format(t=self._t), (_vector_literal(query_vec), list(namespaces), limit)).fetchall()
        return [RetrievalResult(record=_record(r), score=float(r[8])) for r in rows]

    # -- atomic rebuild lifecycle ------------------------------------------
    def empty(self) -> "PgVectorContextStore":
        """An unpublished replacement: every row deleted inside its own
        transaction, invisible to readers until ``publish()``."""
        staging = PgVectorContextStore(self._dsn, dim=self.dim, table=self.table, _staging=True)
        staging.clear()
        return staging

    def publish(self) -> None:
        if self._staging:
            self._conn.commit()
            self._conn.autocommit = True
            self._staging = False

    def discard(self) -> None:
        if self._staging:
            self._conn.rollback()
            self.close()

    def close(self) -> None:
        self._conn.close()
