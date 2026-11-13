"""Aggregate manual audit labels into results/audit/audit_summary.md.

Run from starter/:  python scripts/summarize_audit.py
"""
import csv
from collections import defaultdict
from pathlib import Path

A = Path(__file__).resolve().parents[1] / "results/audit"


def read(name):
    return list(csv.DictReader((A / name).open(encoding="utf-8")))


coh, grd, meta = read("coherence_labels.csv"), read("grounding_labels.csv"), read("metadata_labels.csv")
lines = ["# Manual audit summary", "",
         "Labeller: Claude (AI assistant) following GUIDE.md §4 rubrics; the student must review every label.", "",
         "| Strategy | Coherence mean /2 (n) | Metadata complete & correct | Filter compliance (test, top-3) | Grounding 0/1/2 | Grounding mean /2 |",
         "|---|---|---|---|---|---|"]
for s in ("A", "B", "C"):
    c = [int(r["coherence_0_2"]) for r in coh if r["strategy"] == s]
    m = [r for r in meta if r["strategy"] == s]
    ok = sum(all(r[k] == "True" for k in ("manual_source_ok", "manual_page_ok", "manual_part_ok", "manual_articles_ok")) for r in m)
    fq = defaultdict(list)
    for r in m:
        if r["filter_part"]:
            fq[r["qid"]].append(r["in_filter_scope"] == "True")
    fc = f"{sum(all(v) for v in fq.values())}/{len(fq)}" if fq else "N/A"
    g = [int(r["grounding_0_2"]) for r in grd if r["strategy"] == s]
    lines.append(f"| {s} | {sum(c) / len(c):.2f} ({len(c)}) | {ok}/{len(m)} | {fc} | "
                 f"{g.count(0)}/{g.count(1)}/{g.count(2)} | {sum(g) / len(g):.2f} ({len(g)}) |")
(A / "audit_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
