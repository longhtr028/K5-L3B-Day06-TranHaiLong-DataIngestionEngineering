"""Read and normalize the PDF corpus, keeping page and legal-structure traceability.

The source PDF is a Word export: body text is 14 pt, footnotes (amendment history) are
12 pt, footnote reference markers are 8-9 pt. We keep only body-sized glyphs so that
footnote markers are not glued into words ("thân gỗ300;") and footnote prose does not
leak into the body text. Excluded characters are counted per page in the ingest log.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from .models import DocumentPage

BODY_MIN_PT = 13.0
PARTS = {"nhất": 1, "hai": 2, "ba": 3, "tư": 4, "năm": 5, "sáu": 6}

# Structural headings of a Vietnamese statute, matched at line start.
RE_PART = re.compile(r"^Phần thứ (nhất|hai|ba|tư|năm|sáu)\b", re.M)
RE_CHAPTER = re.compile(r"^Chương ([IVXLC]+)\b", re.M)
RE_ARTICLE = re.compile(r"^Điều (\d+[a-zđ]?)\.", re.M)

logging.getLogger("pypdf").setLevel(logging.ERROR)


def normalize_text(text: str) -> str:
    """NFC-normalize, unify whitespace inside lines, keep line breaks (structure signal).

    Does not lowercase or strip diacritics: Vietnamese meaning depends on them.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace(" ", " ").replace("​", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def flat(text: str) -> str:
    """Whitespace-collapsed form used for evidence matching (same as benchmark/verify_evidence.py)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract every physical page (1-based) with body-only text and extraction stats."""
    reader = PdfReader(path)
    pages: list[DocumentPage] = []
    for number, page in enumerate(reader.pages, start=1):
        kept: list[str] = []
        stats = {"body_chars_raw": 0, "excluded_small_font_chars": 0}

        def visit(text, cm, tm, font_dict, font_size):
            size = font_size * abs(tm[3]) if tm[3] else font_size
            if size >= BODY_MIN_PT or text.strip() == "":
                kept.append(text)
                stats["body_chars_raw"] += len(text.strip())
            else:
                stats["excluded_small_font_chars"] += len(text.strip())

        try:
            page.extract_text(visitor_text=visit)
            text = normalize_text("".join(kept))
            status = "ok" if text else "empty_body"
            error = None
        except Exception as exc:  # recorded, never silently dropped
            text, status, error = "", "extract_error", repr(exc)
        # Drop a leading bare page number line if present ("19").
        text = re.sub(rf"^{number}\n", "", text)
        pages.append(DocumentPage(
            document_id=document_id, source=source, page=number, text=text,
            metadata={"status": status, "error": error, "chars": len(text),
                      "excluded_small_font_chars": stats["excluded_small_font_chars"]},
        ))
    return pages


def annotate_structure(pages: list[DocumentPage]) -> list[DocumentPage]:
    """Split pages at 'Phần thứ ...' headings and attach part/chapter/article markers.

    Output units are page *segments*: same physical page number, but each segment lies in
    exactly one Part, so every chunk derived from it has a single reliable `part` value.
    `metadata["markers"]` holds (offset, article, chapter) for each article/chapter heading
    inside the segment, so chunkers can compute which articles a chunk overlaps.
    Part 0 = preamble before 'Phần thứ nhất'.
    """
    part, chapter, article = 0, None, None
    segments: list[DocumentPage] = []
    for p in pages:
        if not p.text:
            continue
        cuts = [0] + [m.start() for m in RE_PART.finditer(p.text) if m.start() > 0] + [len(p.text)]
        for seg_idx, (a, b) in enumerate(zip(cuts, cuts[1:])):
            text = p.text[a:b].rstrip("\n")
            m = RE_PART.match(text)
            if m:
                part, chapter, article = PARTS[m.group(1)], None, None
            start_state = {"part": part, "chapter": chapter, "article": article}
            markers = []
            for h in sorted(list(RE_CHAPTER.finditer(text)) + list(RE_ARTICLE.finditer(text)),
                            key=lambda x: x.start()):
                if h.re is RE_CHAPTER:
                    chapter, article = h.group(1), None
                else:
                    article = h.group(1)
                markers.append((h.start(), article, chapter))
            segments.append(DocumentPage(
                document_id=p.document_id, source=p.source, page=p.page, text=text,
                metadata={"segment": seg_idx, "part": part, "start": start_state, "markers": markers},
            ))
    return segments


def structure_span(seg: DocumentPage, start: int, end: int) -> dict:
    """Chapter at `start` and every article whose text overlaps [start, end)."""
    st = seg.metadata["start"]
    chapter, article = st["chapter"], st["article"]
    articles: list[str] = []
    for off, art, chap in seg.metadata["markers"]:
        if off <= start:
            chapter, article = chap, art
        elif off < end and art and art not in articles:
            articles.append(art)
    if article:
        articles.insert(0, article)
    return {"part": seg.metadata["part"], "chapter": chapter, "articles": list(dict.fromkeys(articles))}
