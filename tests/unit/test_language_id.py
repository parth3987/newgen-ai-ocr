from __future__ import annotations

import pytest

from newgen_ocr.evaluation.corpus import load_lines
from newgen_ocr.ocr.language_id import devanagari_language, identify, script_shares
from tests.conftest import ROOT

TEXT = ROOT / "evaluation" / "corpus" / "text"


@pytest.mark.parametrize("language", ["en", "hi", "mr", "te", "ta", "kn"])
@pytest.mark.parametrize("lines", [16, 2])
def test_corpus_text_identified(language: str, lines: int) -> None:
    guess = identify("\n".join(load_lines(TEXT, language)[:lines]))
    assert guess.languages == [language]
    assert guess.uncertain is False
    assert guess.mixed_script is False


def test_script_shares() -> None:
    assert script_shares("") == {}
    assert script_shares("abc 123 ...") == {"Latin": 1.0}
    shares = script_shares("ab कि")  # combining vowel sign counts with its script
    assert shares == {"Latin": 0.5, "Devanagari": 0.5}


def test_mixed_script_page() -> None:
    hi = load_lines(TEXT, "hi")[:4]
    en = load_lines(TEXT, "en")[:4]
    guess = identify("\n".join(hi + en))
    assert guess.mixed_script is True
    assert set(guess.languages) == {"hi", "en"}


def test_devanagari_without_evidence_is_ambiguous_not_guessed() -> None:
    assert devanagari_language("राम श्याम") == (None, 0, 0)
    guess = identify("राम श्याम")
    assert guess.languages == ["hi", "mr"]
    assert guess.uncertain is True
    assert guess.reason.startswith("devanagari_ambiguous")


def test_balanced_devanagari_evidence_is_ambiguous() -> None:
    assert devanagari_language("है और आहे आणि")[0] is None


def test_unsupported_script_in_text_is_flagged() -> None:
    guess = identify("এই চালানটি পণ্য সরবরাহের নিশ্চয়তা দেয়")  # Bengali
    assert guess.uncertain is True
    assert guess.reason == "unsupported_script_in_text"
    assert guess.scripts.get("Other") == 1.0


def test_no_text() -> None:
    guess = identify("  12 34 ")
    assert (guess.languages, guess.uncertain, guess.reason) == ([], True, "no_text")
