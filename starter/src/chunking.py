"""Fixed-size and structure-aware chunking over page segments.

Both chunkers work on one page segment at a time, so every chunk maps to exactly one
physical page and one Part. Chunk IDs are deterministic (document, page, segment, kind,
ordinal) so re-ingesting the same source overwrites instead of duplicating.
"""
from __future__ import annotations

import hashlib
import re

from .ingestion import structure_span
from .models import Chunk, DocumentPage

# A new paragraph starts at a structural marker of the statute ...
RE_PARA_START = re.compile(
    r"^(Phần thứ |Chương [IVXLC]+\b|Mục \d+\b|Điều \d+[a-zđ]?\.|\d+[a-zđ]?\.\s|[a-zđ]\)\s)"
)
# ... or after a line that closes a sentence/clause.
RE_LINE_END = re.compile(r"[.;:]$")
RE_HEADING = re.compile(r"^(Phần thứ |Chương [IVXLC]+\b|Mục \d+\b|Điều \d+[a-zđ]?\.)")


def _chunk(seg: DocumentPage, kind: str, idx: int, start: int, end: int, text: str, **extra) -> Chunk:
    meta = structure_span(seg, start, end)
    meta.update(segment=seg.metadata["segment"], char_start=start, char_end=end, kind=kind, **extra)
    meta["content_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    cid = f"{seg.document_id}:p{seg.page:03d}:s{seg.metadata['segment']}:{kind}:{idx:03d}"
    return Chunk(chunk_id=cid, document_id=seg.document_id, source=seg.source,
                 page=seg.page, text=text, metadata=meta)


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Character windows of `size` with `overlap`, ignoring structure (baseline A).

    Operates on the segment text with line breaks flattened to spaces; offsets refer to
    the segment text (1:1 because '\n' -> ' ' keeps length).
    """
    if not 0 <= overlap < size:
        raise ValueError("need 0 <= overlap < size")
    text = page.text.replace("\n", " ")
    step, chunks, start = size - overlap, [], 0
    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end].strip()
        if piece:
            chunks.append(_chunk(page, f"fixed{size}o{overlap}", len(chunks), start, end, piece))
        if end == len(text):
            break
        start += step
    return chunks


def paragraphs(text: str) -> list[tuple[int, int]]:
    """(start, end) offsets of logical paragraphs, rebuilt from wrapped PDF lines."""
    spans: list[list[int]] = []
    pos, prev = 0, ""
    for line in text.split("\n"):
        start, end = pos, pos + len(line)
        if not spans or RE_PARA_START.match(line) or RE_LINE_END.search(prev):
            spans.append([start, end])
        else:
            spans[-1][1] = end
        prev, pos = line, end + 1
    return [(a, b) for a, b in spans]


def _split_long(text: str, a: int, b: int, max_size: int) -> list[tuple[int, int]]:
    """Split an oversized paragraph at clause/sentence ends, else at whitespace."""
    out, start = [], a
    while b - start > max_size:
        window = text[start:start + max_size]
        cut = max(window.rfind("; "), window.rfind(". "), window.rfind(", "))
        if cut < max_size // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_size - 1
        out.append((start, start + cut + 1))
        start = start + cut + 1
        while start < b and text[start] in " \n":
            start += 1
    out.append((start, b))
    return out


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Group consecutive paragraphs up to `max_size` chars (strategy B/C).

    Hard boundaries: a heading (Phần/Chương/Mục/Điều) always starts a new chunk, and the
    heading line stays attached to the paragraphs that follow it. Paragraphs longer than
    `max_size` are split at '; ' / '. ' / ', ' boundaries.
    """
    text = page.text
    units: list[tuple[int, int, bool]] = []
    for a, b in paragraphs(text):
        heading = bool(RE_HEADING.match(text[a:b]))
        for i, (x, y) in enumerate(_split_long(text, a, b, max_size)):
            units.append((x, y, heading and i == 0))

    groups: list[list[int]] = []
    pending_heading = False
    for x, y, is_heading in units:
        fits = bool(groups) and y - groups[-1][0] <= max_size
        if fits and (pending_heading or not is_heading):
            groups[-1][1] = y  # same article, or body attached to the heading line above it
        else:
            groups.append([x, y])
        pending_heading = is_heading and (y - x) < 200  # short heading line waits for its body
    kind = f"para{max_size}"
    return [_chunk(page, kind, i, a, b, text[a:b].strip()) for i, (a, b) in enumerate(groups)
            if text[a:b].strip()]
