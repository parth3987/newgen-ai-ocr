"""Text accuracy metrics: character and word error rates (docs/ACCURACY_EVALUATION.md).

Text is normalised before comparison: Unicode NFC, all whitespace runs collapsed to one
space within a line, blank lines dropped, lines joined with "\\n". No case folding and no
punctuation removal: those are real recognition errors for document data.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import numpy as np

_SPACE = re.compile(r"[^\S\n]+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    lines = (_SPACE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def edit_distance(reference: Sequence[int], hypothesis: Sequence[int]) -> int:
    """Levenshtein distance; each DP row is computed with vectorised NumPy operations."""
    ref = np.asarray(reference, dtype=np.int64)
    hyp = np.asarray(hypothesis, dtype=np.int64)
    if ref.size == 0:
        return int(hyp.size)
    if hyp.size == 0:
        return int(ref.size)
    positions = np.arange(hyp.size + 1, dtype=np.int64)
    previous = positions.copy()
    for i, symbol in enumerate(ref, start=1):
        substitution = previous[:-1] + (hyp != symbol)
        deletion = previous[1:] + 1
        current = np.empty_like(previous)
        current[0] = i
        current[1:] = np.minimum(substitution, deletion)
        # Insertions: current[j] = min over k<=j of current[k] + (j - k).
        current = np.minimum.accumulate(current - positions) + positions
        previous = current
    return int(previous[-1])


def _codes(text: str) -> list[int]:
    return [ord(ch) for ch in text]


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate: edits / reference length (may exceed 1.0)."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    return edit_distance(_codes(ref), _codes(hyp)) / max(len(ref), 1)


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate over whitespace-separated tokens."""
    vocabulary: dict[str, int] = {}
    ref = [vocabulary.setdefault(w, len(vocabulary)) for w in normalize(reference).split()]
    hyp = [vocabulary.setdefault(w, len(vocabulary)) for w in normalize(hypothesis).split()]
    return edit_distance(ref, hyp) / max(len(ref), 1)


def agreement(a: str, b: str) -> float:
    """Symmetric similarity of two hypotheses: 1 - edits / longer length, in [0, 1]."""
    na, nb = normalize(a), normalize(b)
    longest = max(len(na), len(nb))
    if longest == 0:
        return 1.0
    return 1.0 - edit_distance(_codes(na), _codes(nb)) / longest
