"""Automatic retrieval scoring (Hit@1, Evidence Hit@3) with full per-query traces.

A retrieved chunk is a *hit* when its source and page match a gold or documented
alternative gold, AND the whole evidence quote is contained in the chunk text after
whitespace/NFC normalization. Partial containment is recorded as `evidence_split`
(diagnostic only, never counted as a hit). Coherence and grounding are NOT inferred
here: they are labelled by hand in results/audit/.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .ingestion import flat

PROBE = 30  # chars from each end of the quote used to detect a split evidence


def gold_list(q: dict[str, Any]) -> list[dict[str, Any]]:
    golds = [{"source_id": q["gold_source_id"], "page": q["gold_page"], "evidence_quote": q["evidence_quote"],
              "kind": "gold"}]
    golds += [{**g, "kind": "alt"} for g in q.get("alt_gold", [])]
    return golds


def judge_hit(hit: dict[str, Any], golds: list[dict[str, Any]]) -> dict[str, Any]:
    text = flat(hit["text"])
    best = {"hit": False, "page_match": False, "evidence_split": False, "matched": None}
    for g in golds:
        if hit["document_id"] != g["source_id"] or hit["page"] != g["page"]:
            continue
        best["page_match"] = True
        quote = flat(g["evidence_quote"])
        if quote in text:
            return {"hit": True, "page_match": True, "evidence_split": False, "matched": g["kind"]}
        if quote[:PROBE] in text or quote[-PROBE:] in text:
            best["evidence_split"] = True
    return best


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    golds = gold_list(question)
    judged = [judge_hit(h, golds) for h in hits]
    want_part = (question.get("filter") or {}).get("part")
    return {
        "hit_at_1": bool(judged and judged[0]["hit"]),
        "hit_at_3": any(j["hit"] for j in judged[:3]),
        "first_hit_rank": next((i + 1 for i, j in enumerate(judged) if j["hit"]), None),
        "gold_page_in_top3": any(j["page_match"] for j in judged[:3]),
        "evidence_split_in_top3": any(j["evidence_split"] for j in judged[:3]),
        # Scope compliance is measured for every strategy, whether or not it applied the filter.
        "filter_part": want_part,
        "top3_in_filter_scope": None if want_part is None else all(h["part"] == want_part for h in hits[:3]),
        "judgements": judged,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    acc: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in traces:
        for split in (t["split"], "all"):
            for subset in ("all", "filter" if t["filter"] else "no_filter"):
                a = acc[(t["strategy"], f"{split}|{subset}")]
                a["N"] += 1
                a["hit1"] += t["eval"]["hit_at_1"]
                a["hit3"] += t["eval"]["hit_at_3"]
                a["gold_page_top3"] += t["eval"]["gold_page_in_top3"]
                a["split_evidence"] += t["eval"]["evidence_split_in_top3"]
                if t["eval"]["top3_in_filter_scope"] is not None:
                    a["filter_N"] += 1
                    a["filter_ok"] += t["eval"]["top3_in_filter_scope"]
    out: dict[str, Any] = {}
    for (strategy, key), a in sorted(acc.items()):
        split, subset = key.split("|")
        row = dict(a)
        row["hit1_rate"] = round(a["hit1"] / a["N"], 3)
        row["hit3_rate"] = round(a["hit3"] / a["N"], 3)
        out.setdefault(strategy, {}).setdefault(split, {})[subset] = row
    return out
