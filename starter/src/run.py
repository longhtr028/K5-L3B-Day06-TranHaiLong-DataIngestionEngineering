"""Day 06 pipeline: ingest -> chunk -> embed -> pgvector -> benchmark A/B/C.

Run from starter/:
    python -m src.run ingest              # idempotent; run twice to prove no duplicates
    python -m src.run bench --split dev   # choose the strategy on dev only
    python -m src.run bench               # dev + test, writes traces and metrics
    python -m src.run all                 # ingest + bench
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from . import chunking
from .embeddings import E5Embedder
from .evaluation import evaluate_query, summarize
from .ingestion import annotate_structure, load_pdf
from .store import PgVectorStore

STARTER = Path(__file__).resolve().parents[1]
ROOT = STARTER.parent
CONFIG = STARTER / "configs/strategies.json"
MANIFEST = ROOT / "data/manifest.json"
QUESTIONS = ROOT / "benchmark/questions.jsonl"
RESULTS = STARTER / "results"
MIN_CONTENT_CHARS = 500
FRONT_MATTER_PAGES = {1}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_corpus() -> tuple[list, list, list[dict]]:
    """Return (pages, segments, page_log). Fails loudly if < 30 content pages."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pages, page_log = [], []
    for doc in manifest["documents"]:
        path = ROOT / doc["source_path_or_url"]
        doc_pages = load_pdf(path, doc["source_id"], doc["source_path_or_url"])
        digest = sha256(path)
        for p in doc_pages:
            counted = (p.metadata["status"] == "ok" and p.metadata["chars"] >= MIN_CONTENT_CHARS
                       and p.page not in FRONT_MATTER_PAGES)
            reason = ("front_matter" if p.page in FRONT_MATTER_PAGES else
                      "counted" if counted else
                      p.metadata["status"] if p.metadata["status"] != "ok" else "too_short")
            page_log.append({"source_id": p.document_id, "page_id": f"{p.document_id}:p{p.page:03d}",
                             "page": p.page, "body_chars": p.metadata["chars"],
                             "excluded_footnote_chars": p.metadata["excluded_small_font_chars"],
                             "status": p.metadata["status"], "error": p.metadata["error"] or "",
                             "counted_as_content": counted, "reason": reason, "file_sha256": digest})
        pages += doc_pages
    n_content = sum(r["counted_as_content"] for r in page_log)
    if n_content < 30:
        raise SystemExit(f"Only {n_content} content pages; the lab requires >= 30.")
    return pages, annotate_structure(pages), page_log


def build_chunks(segments, spec: dict) -> list:
    if spec["chunker"] == "fixed_size":
        fn = lambda s: chunking.fixed_size(s, spec["size"], spec["overlap"])
    elif spec["chunker"] == "by_paragraph":
        fn = lambda s: chunking.by_paragraph(s, spec["max_size"])
    else:
        raise ValueError(spec["chunker"])
    return [c for s in segments for c in fn(s)]


def length_stats(values: list[int]) -> dict:
    q = statistics.quantiles(values, n=10)
    return {"min": min(values), "p10": round(q[0]), "median": round(statistics.median(values)),
            "mean": round(statistics.mean(values), 1), "p90": round(q[-1]), "max": max(values),
            "total": sum(values)}


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def ingest(cfg: dict, embedder: E5Embedder, store: PgVectorStore) -> dict:
    t0 = time.perf_counter()
    pages, segments, page_log = load_corpus()
    t_read = time.perf_counter() - t0
    write_csv(ROOT / "data/manifest_pages.csv", page_log)
    errors = [r for r in page_log if r["status"] != "ok"]
    write_csv(RESULTS / "ingest_errors.csv", errors or [{"page_id": "", "status": "none"}])

    store.create_schema()
    run = {"run_at": now(), "read_seconds": round(t_read, 2), "physical_pages": len(pages),
           "content_pages": sum(r["counted_as_content"] for r in page_log), "segments": len(segments),
           "empty_or_error_pages": [r["page"] for r in errors], "indexes": {}}
    doc_ids = sorted({s.document_id for s in segments})
    for name, spec in cfg["indexes"].items():
        t1 = time.perf_counter()
        chunks = build_chunks(segments, spec)
        t_chunk = time.perf_counter() - t1
        lens = [len(c.text) for c in chunks]
        tokens = embedder.token_counts([c.text for c in chunks])
        t_embed, totals = 0.0, {"written_new_vector": 0, "metadata_only": 0, "deleted_obsolete": 0}
        for doc_id in doc_ids:
            doc_chunks = [c for c in chunks if c.document_id == doc_id]
            stored = store.existing_hashes(name, doc_id)
            to_embed = [i for i, c in enumerate(doc_chunks)
                    if stored.get(c.chunk_id) != (c.metadata["content_hash"], embedder.model_name)]
            t2 = time.perf_counter()
            new_vecs = embedder.embed_documents([doc_chunks[i].text for i in to_embed]) if to_embed else []
            t_embed += time.perf_counter() - t2
            vectors = [None] * len(doc_chunks)
            for i, v in zip(to_embed, new_vecs):
                vectors[i] = v
            for k, v in store.upsert(name, doc_chunks, vectors, embedder.model_name, doc_id).items():
                totals[k] += v
        rows_after = store.count(name)
        if rows_after != len(chunks):
            raise RuntimeError(f"{name}: {rows_after} rows stored but {len(chunks)} chunks built")
        run["indexes"][name] = {
            "spec": spec, "chunks": len(chunks), "rows_in_store": rows_after,
            "chars": length_stats(lens), "tokens_with_prefix": length_stats(tokens),
            "truncated_over_max_tokens": sum(t > embedder.max_tokens for t in tokens),
            "chunk_seconds": round(t_chunk, 2), "embed_seconds": round(t_embed, 2),
            "index_total_seconds": round(time.perf_counter() - t1, 2), **totals,
        }
        out = RESULTS / "chunks" / f"{name}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps({"chunk_id": c.chunk_id, "page": c.page, **c.metadata, "text": c.text},
                                          ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
        print(f"[ingest] {name}: {len(chunks)} chunks, new vectors {totals['written_new_vector']}, "
              f"unchanged {totals['metadata_only']}, deleted {totals['deleted_obsolete']}, "
              f"embed {t_embed:.1f}s, rows in store {rows_after}")
    run["total_seconds"] = round(time.perf_counter() - t0, 2)
    log_path = RESULTS / "ingest_runs.json"
    history = json.loads(log_path.read_text()) if log_path.exists() else []
    history.append(run)
    log_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return run


def bench(cfg: dict, embedder: E5Embedder, store: PgVectorStore, split: str | None) -> dict:
    questions = [json.loads(l) for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
    if split:
        questions = [q for q in questions if q["split"] == split]
    traces, t0 = [], time.perf_counter()
    qvecs = {q["id"]: embedder.embed_query(q["question"]) for q in questions}  # question text only
    t_embed = time.perf_counter() - t0
    latency: dict[str, list[float]] = {}
    for sname, strat in cfg["strategies"].items():
        for q in questions:
            filters = None
            if strat["use_filter"] and q.get("filter"):
                filters = {k: v for k, v in q["filter"].items() if k in strat["filter_fields"]}
            t1 = time.perf_counter()
            hits = store.search(strat["index"], qvecs[q["id"]], cfg["top_k"], filters)
            latency.setdefault(sname, []).append((time.perf_counter() - t1) * 1000)
            ev = evaluate_query(q, hits)
            traces.append({
                "strategy": sname, "index": strat["index"], "qid": q["id"], "split": q["split"],
                "type": q["type"], "question": q["question"], "filter": q.get("filter"),
                "filter_applied": filters, "gold_page": q["gold_page"],
                "alt_pages": [g["page"] for g in q.get("alt_gold", [])],
                "evidence_quote": q["evidence_quote"], "expected_answer": q["expected_answer"],
                "results": [{"rank": i + 1, "chunk_id": h["chunk_id"], "score": h["score"],
                             "document_id": h["document_id"], "source": h["source"], "page": h["page"],
                             "part": h["part"], "chapter": h["chapter"], "articles": h["articles"],
                             "text": h["text"], **ev["judgements"][i]} for i, h in enumerate(hits)],
                "eval": {k: v for k, v in ev.items() if k != "judgements"},
            })
    tag = split or "all"
    out_dir = RESULTS / "traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"traces_{tag}.jsonl").write_text(
        "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in traces), encoding="utf-8")
    metrics = {"run_at": now(), "split_arg": tag, "n_questions": len(questions),
               "model": embedder.model_name, "model_revision": embedder.revision,
               "query_embed_seconds": round(t_embed, 2),
               "search_ms_median": {k: round(statistics.median(v), 2) for k, v in latency.items()},
               "summary": summarize(traces)}
    (RESULTS / f"metrics_{tag}.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    write_metrics_md(metrics, RESULTS / f"metrics_{tag}.md")
    print((RESULTS / f"metrics_{tag}.md").read_text(encoding="utf-8"))
    return metrics


def write_metrics_md(metrics: dict, path: Path) -> None:
    lines = [f"# Retrieval metrics ({metrics['split_arg']}) — {metrics['run_at']}", "",
             "| Strategy | Split | Subset | Hit@1 | Evidence Hit@3 | Gold page in top-3 | Top-3 in filter scope |",
             "|---|---|---|---|---|---|---|"]
    for s, splits in metrics["summary"].items():
        for split in ("dev", "test", "all"):
            for subset in ("all", "filter", "no_filter"):
                r = splits.get(split, {}).get(subset)
                if not r:
                    continue
                scope = f"{r['filter_ok']}/{r['filter_N']}" if r.get("filter_N") else "N/A"
                lines.append(f"| {s} | {split} | {subset} | {r['hit1']}/{r['N']} | {r['hit3']}/{r['N']} | "
                             f"{r['gold_page_top3']}/{r['N']} | {scope} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["ingest", "bench", "all"])
    ap.add_argument("--split", choices=["dev", "test"], default=None)
    args = ap.parse_args()

    load_dotenv(STARTER / ".env")
    cfg = load_config()
    emb_cfg = cfg["embedding"]
    model = os.getenv("EMBEDDING_MODEL") or emb_cfg["model"]
    if model != emb_cfg["model"]:
        raise SystemExit(f"EMBEDDING_MODEL={model} differs from configs/strategies.json ({emb_cfg['model']})")
    embedder = E5Embedder(model, device=emb_cfg["device"])
    store = PgVectorStore(os.environ["DATABASE_URL"], embedder.dimensions)
    env = {"python": sys.version.split()[0], "platform": platform.platform(), **store.server_versions(),
           "model": model, "model_revision": embedder.revision, "dimensions": embedder.dimensions,
           "max_tokens": embedder.max_tokens, "cost_usd": 0.0}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
    print("[env]", env)
    try:
        if args.step in ("ingest", "all"):
            ingest(cfg, embedder, store)
        if args.step in ("bench", "all"):
            bench(cfg, embedder, store, args.split)
    finally:
        store.close()


if __name__ == "__main__":
    main()
