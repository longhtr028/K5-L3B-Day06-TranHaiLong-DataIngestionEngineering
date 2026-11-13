"""Verify every gold evidence quote against the extracted PDF text.

Usage (from repo root):
    starter/.venv/bin/python benchmark/verify_evidence.py
"""
import csv
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "data/raw/luat-shtt-hn-2026.pdf"
QUESTIONS = ROOT / "benchmark/questions.jsonl"
OUT = ROOT / "benchmark/evidence_check.csv"
BODY_MIN_PT = 13  # body text is 14 pt, footnotes 12 pt, footnote markers 8-9 pt
PARTS = {"nhất": 1, "hai": 2, "ba": 3, "tư": 4, "năm": 5, "sáu": 6}

logging.getLogger("pypdf").setLevel(logging.ERROR)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def extract(reader: PdfReader) -> tuple[list[str], list[str]]:
    plain, body = [], []
    for page in reader.pages:
        plain.append(norm(page.extract_text() or ""))
        parts: list[str] = []

        def visit(text, cm, tm, font_dict, font_size):
            size = font_size * abs(tm[3]) if tm[3] else font_size
            if size >= BODY_MIN_PT:
                parts.append(text)

        page.extract_text(visitor_text=visit)
        body.append(norm("".join(parts)))
    return plain, body


def part_index(body: list[str]) -> list[list[tuple[int, int]]]:
    """Per page: (offset, part) for every 'Phần thứ ...' heading, plus the part in force at offset 0."""
    current, out = 0, []
    for text in body:
        marks = [(0, current)]
        for m in re.finditer(r"Phần thứ (nhất|hai|ba|tư|năm|sáu)\b", text):
            current = PARTS[m.group(1)]
            marks.append((m.start(), current))
        out.append(marks)
    return out


def part_at(marks: list[tuple[int, int]], offset: int) -> int:
    return [p for o, p in marks if o <= offset][-1]


def main() -> int:
    plain, body = extract(PdfReader(PDF))
    marks = part_index(body)
    questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows, failures = [], 0
    for q in questions:
        golds = [{"page": q["gold_page"], "evidence_quote": q["evidence_quote"], "kind": "gold"}]
        golds += [{**g, "kind": "alt"} for g in q.get("alt_gold", [])]
        for g in golds:
            quote, page = norm(g["evidence_quote"]), g["page"]
            in_plain = quote in plain[page - 1]
            pos = body[page - 1].find(quote)
            pages_found = [i + 1 for i, t in enumerate(body) if quote in t]
            part = part_at(marks[page - 1], pos) if pos >= 0 else None
            want = (q.get("filter") or {}).get("part")
            # Gold must exist in body-only text (the text the pipeline embeds); plain extract_text()
            # glues footnote markers into words ("thân gỗ300;"), so it is reported but not required.
            ok = pos >= 0 and (want is None or g["kind"] == "alt" or want == part)
            failures += not ok
            rows.append({
                "id": q["id"], "kind": g["kind"], "split": q.get("split"), "page": page,
                "quote_chars": len(quote), "in_plain_text": in_plain, "in_body_text": pos >= 0,
                "pages_containing_quote": " ".join(map(str, pages_found)),
                "part_at_quote": part, "filter_part": want, "ok": ok,
            })
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        if not r["ok"]:
            print("FAIL", r)
    print(f"{len(rows) - failures}/{len(rows)} evidence quotes verified -> {OUT.relative_to(ROOT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
