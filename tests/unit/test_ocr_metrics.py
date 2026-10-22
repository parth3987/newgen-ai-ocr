from __future__ import annotations

import random
import unicodedata

import pytest

from newgen_ocr.evaluation.metrics import agreement, cer, edit_distance, normalize, wer


def _reference_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


@pytest.mark.parametrize("seed", range(25))
def test_vectorised_distance_matches_reference(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - test data, not cryptography
    alphabet = "abcde अआक"
    a = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
    b = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
    assert edit_distance([ord(c) for c in a], [ord(c) for c in b]) == _reference_distance(a, b)


def test_cer_and_wer() -> None:
    assert cer("hello world", "hello world") == 0.0
    assert cer("hello world", "hallo world") == pytest.approx(1 / 11)
    assert cer("abc", "") == 1.0
    assert cer("", "") == 0.0
    assert wer("total amount due", "total amount due") == 0.0
    assert wer("total amount due", "total amont due") == pytest.approx(1 / 3)
    assert cer("ab", "abcdef") == 2.0  # insertions can exceed 1.0


def test_normalisation() -> None:
    assert normalize("  a\t b \n\n  c  ") == "a b\nc"
    decomposed = unicodedata.normalize("NFD", "café")
    assert cer("café", decomposed) == 0.0
    # Devanagari: NFC composes nukta forms, so equivalent encodings compare equal.
    assert cer("क़", "क़") == 0.0
    # Case and punctuation are kept: they are real errors in document data.
    assert cer("Total:", "total") > 0


def test_agreement() -> None:
    assert agreement("same text", "same text") == 1.0
    assert agreement("", "") == 1.0
    assert agreement("abc", "") == 0.0
    assert agreement("abcd", "abce") == pytest.approx(0.75)
    assert agreement("abcd", "abce") == agreement("abce", "abcd")
