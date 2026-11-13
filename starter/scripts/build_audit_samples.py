"""Build the manual-audit sample sheets (no labels are produced here).

coherence_sample.csv : 10 source positions fixed in advance = midpoint of the body text of
                       pages 10, 20, ..., 100 (independent of any score). For each index the
                       chunk covering that position is exported (first one if windows overlap).
                       C uses the same index as B, so its chunks are identical to B's.
metadata_sample.csv  : every test question x top-3 x strategy, with stored metadata and an
                       automatic cross-check against the raw PDF (page text containment,
                       nearest preceding 'Điều N.' heading, part from 'Phần thứ' headings).
Run from starter/:  python scripts/build_audit_samples.py
"""
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingestion import annotate_structure, flat, load_pdf  # noqa: E402

S = Path(__file__).resolve().parents[1]
RES = S / "results"
cfg = json.loads((S / "configs/strategies.json").read_text())
pages = load_pdf(S.parent / "data/raw/luat-shtt-hn-2026.pdf", "luat-shtt-hn-2026", "data/raw/luat-shtt-hn-2026.pdf")
segs = annotate_structure(pages)
chunks = {name: [json.loads(l) for l in (RES / f"chunks/{name}.jsonl").open()] for name in cfg["indexes"]}

rows = []
for i, pg in enumerate(range(10, 101, 10), start=1):
    seg = next(s for s in segs if s.page == pg)  # every sampled page has a single segment
    pos = len(seg.text) // 2
    for sname, strat in cfg["strategies"].items():
        c = next(c for c in chunks[strat["index"]]
                 if c["page"] == pg and c["segment"] == seg.metadata["segment"] and c["char_start"] <= pos < c["char_end"])
        rows.append({"position_id": f"pos{i:02d}", "page": pg, "char_offset": pos,
                     "source_context": seg.text[max(0, pos - 60):pos + 60].replace("\n", " "),
                     "strategy": sname, "chunk_id": c["chunk_id"], "chunk_chars": len(c["text"]),
                     "chunk_text": c["text"]})
with (RES / "audit/coherence_sample.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

# --- metadata cross-check (independent path: plain pypdf text of the whole PDF) -------------
body = {p.page: flat(p.text) for p in pages}
full, page_start = "", {}
for p in pages:
    page_start[p.page] = len(full); full += flat(p.text) + " "
part_marks = [(m.start(), m.group(1)) for m in re.finditer(r"Phần thứ (nhất|hai|ba|tư|năm|sáu) ", full)]
PART = {"nhất": 1, "hai": 2, "ba": 3, "tư": 4, "năm": 5, "sáu": 6}
art_marks = [(m.start(), m.group(1)) for m in re.finditer(r"Điều (\d+[a-zđ]?)\. ", full)]

traces = [json.loads(l) for l in (RES / "traces/traces_all.jsonl").open()]
mrows = []
for t in traces:
    if t["split"] != "test":
        continue
    for r in t["results"]:
        txt = flat(r["text"])
        page_ok = txt in body[r["page"]]
        off = page_start[r["page"]] + body[r["page"]].find(txt) if page_ok else None
        exp_part = next((PART[n] for o, n in reversed(part_marks) if off is not None and o <= off), 0)
        exp_arts = []
        if off is not None:
            before = [a for o, a in art_marks if o <= off]
            inside = [a for o, a in art_marks if off < o < off + len(txt)]
            exp_arts = list(dict.fromkeys(before[-1:] + inside))
        mrows.append({"strategy": t["strategy"], "qid": t["qid"], "rank": r["rank"], "chunk_id": r["chunk_id"],
                      "source": r["source"], "page": r["page"], "part": r["part"], "articles": " ".join(r["articles"]),
                      "auto_page_text_match": page_ok, "auto_expected_part": exp_part,
                      "auto_expected_articles": " ".join(exp_arts),
                      "auto_all_ok": page_ok and exp_part == r["part"] and exp_arts == r["articles"],
                      "filter_part": (t["filter"] or {}).get("part", ""), "in_filter_scope":
                      "" if not t["filter"] else r["part"] == t["filter"]["part"]})
with (RES / "audit/metadata_sample.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(mrows[0])); w.writeheader(); w.writerows(mrows)
print(len(rows), "coherence rows;", len(mrows), "metadata rows;",
      sum(r["auto_all_ok"] for r in mrows), "auto-consistent")
