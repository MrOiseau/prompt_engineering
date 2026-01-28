"""
Chunking Utilities for PII Redaction.

Provides logic to split long texts into atomic segments (by turn, sentence, or newline)
to ensure LLM context limits are respected without cutting through entities or context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Dict


# ----------------------------
# Data models
# ----------------------------

Span = Tuple[int, int]  # [start, end) half-open

@dataclass(frozen=True)
class Entity:
    """Represents a PII entity for chunking purposes."""
    type: str
    text: str
    start: int
    end: int

@dataclass(frozen=True)
class Segment:
    """Represents an atomic text segment."""
    start: int
    end: int
    kind: str  # "turn:timestamp" | "turn:speaker" | "sentence" | "newline" | "fallback"
    text: str


# ----------------------------
# Helpers: spans / boundaries
# ----------------------------

def normalize_spans(spans: Iterable[Span], text_len: int) -> List[Span]:
    """Clamp, drop invalid, and merge overlaps to speed boundary checks."""
    cleaned: List[Span] = []
    for s, e in spans:
        if s is None or e is None:
            continue
        s = max(0, min(int(s), text_len))
        e = max(0, min(int(e), text_len))
        if e <= s:
            continue
        cleaned.append((s, e))
    if not cleaned:
        return []

    cleaned.sort(key=lambda x: (x[0], x[1]))
    merged: List[Span] = [cleaned[0]]
    for s, e in cleaned[1:]:
        ps, pe = merged[-1]
        if s <= pe:  # overlap/touch
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def boundary_inside_any_entity(boundary: int, spans: Sequence[Span]) -> bool:
    """
    boundary is a split point between chars: left=[..boundary), right=[boundary..)
    It's forbidden if it lies strictly inside an entity span: start < boundary < end
    Boundaries at exactly start or end are allowed.
    spans is assumed merged and sorted.
    """
    # Binary-ish scan; spans are few in practice, linear is OK, but keep early break.
    for s, e in spans:
        if boundary <= s:
            return False
        if s < boundary < e:
            return True
    return False


def filter_boundaries(boundaries: Iterable[int], spans: Sequence[Span]) -> List[int]:
    out: List[int] = []
    for b in boundaries:
        if not boundary_inside_any_entity(b, spans):
            out.append(b)
    return out


def split_by_boundaries(text: str, boundaries: Sequence[int], kind: str) -> List[Segment]:
    """
    boundaries are end positions (0 < b < len(text)).
    Always returns segments that exactly partition original text.
    """
    if not boundaries:
        return [Segment(0, len(text), kind, text)]

    segs: List[Segment] = []
    last = 0
    for b in boundaries:
        if b <= last:
            continue
        segs.append(Segment(last, b, kind, text[last:b]))
        last = b
    if last < len(text):
        segs.append(Segment(last, len(text), kind, text[last:]))
    return segs


# ----------------------------
# Turn detection (preferred)
# ----------------------------

TS_TURN_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+\w+:\s*", re.MULTILINE)
SPEAKER_TURN_RE = re.compile(r"^[A-Za-z][\w\s'.-]{0,30}:\s+", re.MULTILINE)

def find_turn_starts(text: str) -> Tuple[List[int], str]:
    """
    Returns (starts, kind_base) where starts are turn start indices.
    Chooses timestamped turns if present; otherwise speaker-label turns if present.
    """
    ts = [m.start() for m in TS_TURN_RE.finditer(text)]
    if ts:
        return ts, "turn:timestamp"

    sp = [m.start() for m in SPEAKER_TURN_RE.finditer(text)]
    if sp:
        return sp, "turn:speaker"

    return [], "fallback"


def segments_from_turn_starts(
    text: str,
    starts: Sequence[int],
    kind_base: str,
    entity_spans: Sequence[Span],
) -> List[Segment]:
    """
    Build turn segments using detected starts.
    Any start that would create a forbidden boundary (inside entity) is dropped (merged).
    """
    if not starts:
        return [Segment(0, len(text), kind_base, text)]

    # Ensure starts are unique, sorted, within range, and include 0 if first turn doesn't start at 0.
    uniq = sorted({s for s in starts if 0 <= s < len(text)})
    if not uniq:
        return [Segment(0, len(text), kind_base, text)]
    if uniq[0] != 0:
        uniq = [0] + uniq

    # Candidate boundaries are all turn starts except 0
    boundaries = filter_boundaries(uniq[1:], entity_spans)
    segs = split_by_boundaries(text, boundaries, kind_base)
    return segs


# ----------------------------
# Sentence + newline fallback (with abbreviation protection)
# ----------------------------

DEFAULT_ABBREVIATIONS = {
    # English
    "dr.", "mr.", "mrs.", "ms.", "prof.", "sr.", "jr.", "st.", "no.",
    "etc.", "e.g.", "i.e.",
    # Serbian/region common
    "npr.", "itd.", "itp.", "tj.", "god.", "br.",
}

SENT_END_RE = re.compile(r"([.!?])(\s+)")  # punctuation + whitespace

def protect_dots(text: str, abbreviations: Optional[set[str]] = None) -> str:
    """
    Replace '.' with a sentinel in contexts where '.' should NOT end a sentence.
    Keeps length identical (1 char -> 1 char), preserving indices.
    """
    if abbreviations is None:
        abbreviations = DEFAULT_ABBREVIATIONS

    sentinel = "∯"  # one char

    # Protect decimal numbers: 3.14 -> 3∯14
    text = re.sub(r"(?<=\d)\.(?=\d)", sentinel, text)

    # Protect initials: "M. A." -> "M∯ A∯"
    text = re.sub(r"\b([A-Za-zА-Яа-я])\.(?=\s)", r"\1" + sentinel, text)

    # Protect known abbreviations (case-insensitive).
    # We protect each '.' inside matched abbreviation by replacing '.' -> sentinel.
    # Build regex for abbreviations with dots, but do it robustly:
    # - sort by length descending to avoid partial matches
    abbr_sorted = sorted(abbreviations, key=len, reverse=True)
    # Escape and make pattern that matches exact abbreviation tokens
    # Example: r'\b(?:dr\.|e\.g\.)'
    abbr_pat = r"\b(?:" + "|".join(re.escape(a) for a in abbr_sorted) + r")"
    abbr_re = re.compile(abbr_pat, re.IGNORECASE)

    def _abbr_repl(m: re.Match) -> str:
        return m.group(0).replace(".", sentinel)

    text = abbr_re.sub(_abbr_repl, text)

    return text


def sentence_and_newline_boundaries(text: str) -> List[int]:
    """
    Produces candidate boundaries (end positions) at:
    - blank lines
    - sentence ends (.,!,?) followed by whitespace
    - single newline boundaries (useful for STT)
    """
    protected = protect_dots(text)

    boundaries: List[int] = []

    i = 0
    n = len(protected)
    while i < n:
        # Blank line boundary: split BEFORE the "\n\n"
        if protected.startswith("\n\n", i):
            if i > 0:
                boundaries.append(i)
            i += 2
            continue

        # Single newline boundary: split at newline (end index includes '\n' or not?)
        # Here we split AFTER '\n' so next segment starts cleanly at next line.
        if protected[i] == "\n":
            boundaries.append(i + 1)
            i += 1
            continue

        # Sentence boundary: end right after punctuation
        m = SENT_END_RE.match(protected, i)
        if m:
            boundaries.append(i + 1)  # include punctuation in left segment
            # Skip punctuation + whitespace
            i = i + 1 + len(m.group(2))
            continue

        i += 1

    # Deduplicate and keep inside (0, len)
    boundaries = sorted({b for b in boundaries if 0 < b < len(text)})
    return boundaries


def segments_from_sentence_fallback(
    text: str,
    entity_spans: Sequence[Span],
) -> List[Segment]:
    boundaries = sentence_and_newline_boundaries(text)
    boundaries = filter_boundaries(boundaries, entity_spans)
    if not boundaries:
        return [Segment(0, len(text), "fallback", text)]

    # We'll label all as "sentence" even though it includes newline splits.
    # If you want, you can post-classify by inspecting the last char of each segment.
    return split_by_boundaries(text, boundaries, "sentence")


# ----------------------------
# Public API
# ----------------------------

def make_atomic_segments(
    text: str,
    entities: Optional[Sequence[Dict]] = None,
    *,
    entity_spans: Optional[Sequence[Span]] = None,
) -> List[Segment]:
    """
    Deterministic atomic segmentation:
    1) Try turn-based segmentation (timestamped, else speaker labels).
    2) Fallback to sentence/newline segmentation with abbreviation protection.
    3) Never cut inside entity spans (if provided).

    Args:
        text: Input text to split.
        entities: Optional list of dicts with 'start'/'end'.
        entity_spans: Optional list of (start, end) tuples.
        
    Returns:
        List of Segment objects.
    """
    if entities is not None:
        spans_in = [(int(e["start"]), int(e["end"])) for e in entities if "start" in e and "end" in e]
    else:
        spans_in = list(entity_spans or [])

    spans = normalize_spans(spans_in, len(text))

    starts, kind_base = find_turn_starts(text)
    if starts:
        segs = segments_from_turn_starts(text, starts, kind_base, spans)
        return segs

    return segments_from_sentence_fallback(text, spans)
