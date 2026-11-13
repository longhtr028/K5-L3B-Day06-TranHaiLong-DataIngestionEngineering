"""PostgreSQL + pgvector store: persistent vectors, metadata columns, SQL-side filtering.

One table holds every index; `index_name` separates chunkings (e.g. fixed500o50, para800).
Primary key (index_name, chunk_id) + deterministic chunk IDs make re-ingest idempotent.
Search is exact (sequential scan, no ANN index) with cosine distance `<=>`.
"""
from __future__ import annotations

import json
from typing import Any

import psycopg

from .models import Chunk

ALLOWED_FILTERS = {"part", "chapter", "article", "page"}


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.url, self.dim = database_url, dimensions
        self.conn = psycopg.connect(database_url, connect_timeout=5, autocommit=False)

    def close(self) -> None:
        self.conn.close()

    def server_versions(self) -> dict[str, str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT current_setting('server_version'), "
                        "(SELECT extversion FROM pg_extension WHERE extname='vector')")
            pg, vec = cur.fetchone()
        self.conn.rollback()
        return {"postgresql": pg, "pgvector": vec}

    def create_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    index_name   text NOT NULL,
                    chunk_id     text NOT NULL,
                    document_id  text NOT NULL,
                    source       text NOT NULL,
                    page         int  NOT NULL,
                    part         int  NOT NULL,
                    chapter      text,
                    articles     text[] NOT NULL DEFAULT '{{}}',
                    text         text NOT NULL,
                    content_hash text NOT NULL,
                    metadata     jsonb NOT NULL DEFAULT '{{}}',
                    embed_model  text NOT NULL,
                    embedding    vector({self.dim}) NOT NULL,
                    ingested_at  timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (index_name, chunk_id)
                )""")
            cur.execute("CREATE INDEX IF NOT EXISTS chunks_filter_idx ON chunks (index_name, part)")
            cur.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid='chunks'::regclass "
                        "AND attname='embedding'")
            (typmod,) = cur.fetchone()
            if typmod != self.dim:
                raise RuntimeError(f"table has vector({typmod}) but model gives {self.dim} dims")
        self.conn.commit()

    def existing_hashes(self, index_name: str, document_id: str) -> dict[str, tuple[str, str]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT chunk_id, content_hash, embed_model FROM chunks "
                        "WHERE index_name=%s AND document_id=%s", (index_name, document_id))
            rows = {cid: (h, m) for cid, h, m in cur.fetchall()}
        self.conn.rollback()
        return rows

    def count(self, index_name: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*), count(DISTINCT chunk_id) FROM chunks WHERE index_name=%s",
                        (index_name,))
            n, distinct = cur.fetchone()
        self.conn.rollback()
        if n != distinct:
            raise RuntimeError(f"duplicate chunk ids in {index_name}")
        return n

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]],
               embed_model: str, document_id: str) -> dict[str, int]:
        """Write chunks for one document in one transaction and delete obsolete ones.

        `vectors[i]` may be None for a chunk whose content hash is unchanged: the stored
        vector is kept and only metadata is refreshed.
        """
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors differ in length")
        ids = [c.chunk_id for c in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate chunk ids in batch")
        stats = {"written_new_vector": 0, "metadata_only": 0, "deleted_obsolete": 0}
        with self.conn.cursor() as cur:
            for c, v in zip(chunks, vectors):
                meta = {k: v_ for k, v_ in c.metadata.items()}
                common = (c.document_id, c.source, c.page, meta["part"], meta.get("chapter"),
                          meta.get("articles", []), c.text, meta["content_hash"], json.dumps(meta, ensure_ascii=False))
                if v is None:
                    cur.execute("""UPDATE chunks SET document_id=%s, source=%s, page=%s, part=%s,
                                   chapter=%s, articles=%s, text=%s, content_hash=%s, metadata=%s
                                   WHERE index_name=%s AND chunk_id=%s""", (*common, strategy, c.chunk_id))
                    if cur.rowcount != 1:
                        raise RuntimeError(f"expected stored vector for {c.chunk_id}")
                    stats["metadata_only"] += 1
                    continue
                if len(v) != self.dim:
                    raise ValueError(f"{c.chunk_id}: vector has {len(v)} dims, expected {self.dim}")
                cur.execute("""
                    INSERT INTO chunks (index_name, chunk_id, document_id, source, page, part, chapter,
                                        articles, text, content_hash, metadata, embed_model, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                    ON CONFLICT (index_name, chunk_id) DO UPDATE SET
                        document_id=EXCLUDED.document_id, source=EXCLUDED.source, page=EXCLUDED.page,
                        part=EXCLUDED.part, chapter=EXCLUDED.chapter, articles=EXCLUDED.articles,
                        text=EXCLUDED.text, content_hash=EXCLUDED.content_hash, metadata=EXCLUDED.metadata,
                        embed_model=EXCLUDED.embed_model, embedding=EXCLUDED.embedding, ingested_at=now()
                """, (strategy, c.chunk_id, *common, embed_model, _vec(v)))
                stats["written_new_vector"] += 1
            # Chunks of this document that no longer exist in the new version (source changed).
            cur.execute("DELETE FROM chunks WHERE index_name=%s AND document_id=%s AND NOT chunk_id = ANY(%s)",
                        (strategy, document_id, ids))
            stats["deleted_obsolete"] = cur.rowcount
        self.conn.commit()
        return stats

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if len(query_vector) != self.dim:
            raise ValueError("query vector dimension mismatch")
        where, params = ["index_name = %s"], [strategy]
        for key, value in (filters or {}).items():
            if key not in ALLOWED_FILTERS:
                raise ValueError(f"filter '{key}' not allowed")
            if key == "article":
                where.append("%s = ANY(articles)")
                params.append(str(value))
            else:
                where.append(f"{key} = %s")  # key is whitelisted above; value is parameterized
                params.append(value)
        sql = f"""SELECT chunk_id, document_id, source, page, part, chapter, articles, text, metadata,
                         1 - (embedding <=> %s::vector) AS score
                  FROM chunks WHERE {' AND '.join(where)}
                  ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s"""
        qv = _vec(query_vector)
        with self.conn.cursor() as cur:
            cur.execute(sql, [qv, *params, qv, top_k])
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        self.conn.rollback()
        for r in rows:
            r["score"] = round(float(r["score"]), 6)
        return rows
