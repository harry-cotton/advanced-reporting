"""The loud-fail number guard (A2) — no number reaches prose unless the
deterministic layer computed it first.

Every numeral in the agent's output must exist in the facts payload after
normalization; on any mismatch the whole artifact is REJECTED and the violations
printed — never published. Matching is EXACT after normalization: no tolerance,
ever ("close enough" is no guard). Ported from the proposal-agent guard, extended
per the 2026-07-11 review:

- number-WORDS ("three channels", "a dozen") normalize to digits and are checked
  like any numeral ("one" is exempt — too ambiguous in prose to police);
- comparative-multiplier words ("doubled", "twice", "half") are rejected outright:
  they assert computations. The engine computes ratios INTO the facts ("2.1x"),
  so the agent cites those instead;
- formatting variants normalize: currency symbols, thousands separators, %, x/×
  suffixes, leading zeros, trailing fractional zeros. Matching is VALUE-exact,
  representation-insensitive: "$74.60" == "74.60" == "74.6" (same value), but a
  rounded "$74.6" for a 74.63 fact — or a computed midpoint — still rejects.
"""
from __future__ import annotations

import json
import re

NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "dozen": 12, "zero": 0,
}
_MULTIPLIER_RE = re.compile(
    r"\b(doubl\w*|tripl\w*|quadrupl\w*|twice|halv\w*|half)\b", re.IGNORECASE)
_NUMERAL_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _canonical(token: str) -> str:
    """Value-canonical numeral string: commas out, leading zeros off integers,
    trailing fractional zeros off decimals ('15.0' -> '15', '74.60' -> '74.6').

    Live finding 2026-07-11: fact values serialize as floats ('15.0') but are
    legitimately cited as '$15' or '15.00' — same VALUE, different rendering.
    Canonicalizing both sides keeps the match value-exact while representation-
    insensitive; a ROUNDED citation ('13.7' for 13.69) still has a different
    canonical value and still rejects."""
    t = token.replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    t = t.lstrip("0") or "0"
    return t


def _words_to_digits(text: str) -> str:
    for word, digit in NUMBER_WORDS.items():
        text = re.sub(rf"\b{word}\b", str(digit), text, flags=re.IGNORECASE)
    return text


def extract_numerals(text: str) -> set[str]:
    """All canonical numeral tokens in ``text`` (number-words included)."""
    return {_canonical(m) for m in _NUMERAL_RE.findall(_words_to_digits(text))}


def fact_numerals(facts) -> set[str]:
    """The allowed set: every numeral extractable from the facts payload
    (dict/list/str — non-strings are serialized first)."""
    blob = facts if isinstance(facts, str) else json.dumps(facts, default=str)
    return extract_numerals(blob)


def check_output(output: str, facts) -> list[str]:
    """Return the list of violations (empty == output may be published)."""
    violations: list[str] = []
    for m in _MULTIPLIER_RE.finditer(output):
        violations.append(
            f"comparative-multiplier word {m.group(0)!r} — cite the computed ratio "
            "from FACTS (e.g. '2.1x') instead")
    allowed = fact_numerals(facts)
    for tok in sorted(extract_numerals(output) - allowed):
        violations.append(f"numeral {tok!r} does not exist in the facts payload")
    return violations
