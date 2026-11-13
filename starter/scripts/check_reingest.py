"""Prove re-ingest semantics on a throwaway index (`reingest_test`), then drop it.

1. Ingest the real para800 chunks of pages 40-49.
2. Ingest the same again          -> expect 0 new vectors, 0 deletions, same row count.
3. Simulate a changed document: page 45 edited, page 49 removed
                                  -> expect new vectors only for edited chunks, page-49 chunks deleted.
Run from starter/:  python scripts/check_reingest.py
"""
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

from src import chunking  # noqa: E402
from src.embeddings import E5Embedder  # noqa: E402
from src.ingestion import annotate_structure, load_pdf  # noqa: E402
from src.store import PgVectorStore  # noqa: E402

STARTER = Path(__file__).resolve().parents[1]
load_dotenv(STARTER / ".env")
NAME, DOC = "reingest_test", "luat-shtt-hn-2026"


def ingest(store, emb, segs):
    chunks = [c for s in segs for c in chunking.by_paragraph(s, 800)]
    stored = store.existing_hashes(NAME, DOC)
    to_embed = [i for i, c in enumerate(chunks) if stored.get(c.chunk_id) != (c.metadata["content_hash"], emb.model_name)]
    vecs = [None] * len(chunks)
    for i, v in zip(to_embed, emb.embed_documents([chunks[i].text for i in to_embed]) if to_embed else []):
        vecs[i] = v
    stats = store.upsert(NAME, chunks, vecs, emb.model_name, DOC)
    return {"chunks": len(chunks), **stats, "rows": store.count(NAME)}


emb = E5Embedder()
store = PgVectorStore(os.environ["DATABASE_URL"], emb.dimensions)
store.create_schema()
pages = load_pdf(STARTER.parent / "data/raw/luat-shtt-hn-2026.pdf", DOC, "data/raw/luat-shtt-hn-2026.pdf")
segs = [s for s in annotate_structure(pages) if 40 <= s.page <= 49]
report = {}
try:
    report["1_first_ingest"] = ingest(store, emb, segs)
    report["2_same_again"] = ingest(store, emb, segs)
    changed = [replace(s, text=s.text.replace("Điều 59.", "Điều 59. [SỬA ĐỔI THỬ]")) if s.page == 45 else s
               for s in segs if s.page != 49]
    report["3_changed_doc"] = ingest(store, emb, changed)
finally:
    with store.conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE index_name=%s", (NAME,))
    store.conn.commit()
    store.close()
ok = (report["2_same_again"]["written_new_vector"] == 0 and report["2_same_again"]["deleted_obsolete"] == 0
      and report["2_same_again"]["rows"] == report["1_first_ingest"]["rows"]
      and report["3_changed_doc"]["deleted_obsolete"] > 0 and report["3_changed_doc"]["written_new_vector"] > 0
      and report["3_changed_doc"]["rows"] == report["3_changed_doc"]["chunks"])
report["ok"] = ok
out = STARTER / "results/reingest_check.json"
out.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
sys.exit(0 if ok else 1)
