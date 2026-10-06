"""Calibration: learn a new person's timing from one known word.

The person sends PARIS, the standard word Morse speeds are measured with
(it's exactly 50 units long, which is where "words per minute" comes from).
Because we know what they meant to send, every press and gap can be labelled
without guessing:

    P  .--.    A  .-    R  .-.    I  ..    S  ...

14 presses (8 dots, 6 dashes), 9 gaps inside characters, 4 between characters.

From those, calibrate() estimates the starting timing for the adaptive decoder
in three layers, from most data to least (see the comments in the function).
Word gaps can't be measured from one word, so they're estimated from the
character gaps.
"""

import math

from .adaptive import TEXTBOOK_UNITS
from .events import Event
from .table import CHAR_TO_PATTERN

CALIBRATION_WORD = "PARIS"

# How strongly per-kind differences are pulled back toward "no difference".
# Equivalent to 3 imaginary measurements that match the textbook exactly.
SHRINK_STRENGTH = 3


class CalibrationError(ValueError):
    pass


def expected_labels(word: str = CALIBRATION_WORD) -> tuple[list[str], list[str]]:
    """The press labels ('.'/'-') and gap labels ('intra'/'char') a perfect
    send of `word` would produce."""
    patterns = [CHAR_TO_PATTERN[c] for c in word.upper()]
    presses: list[str] = []
    gaps: list[str] = []
    for ci, pattern in enumerate(patterns):
        if ci:
            gaps.append("char")
        for si, symbol in enumerate(pattern):
            if si:
                gaps.append("intra")
            presses.append(symbol)
    return presses, gaps


def calibrate(events: list[Event], word: str = CALIBRATION_WORD) -> dict[str, float]:
    """Return starting timing (ms per kind) for the adaptive decoder.

    Raises CalibrationError if the number of presses doesn't match the word,
    which usually means a press was missed or doubled; the fix is to try again.
    """
    press_labels, gap_labels = expected_labels(word)
    presses = [e.duration_ms for e in events if e.is_on]
    gaps = [e.duration_ms for e in events if not e.is_on]

    # Silence before the first press or after the last isn't part of the word.
    if events and not events[0].is_on:
        gaps = gaps[1:]
    gaps = gaps[: max(0, len(presses) - 1)]

    if len(presses) != len(press_labels):
        raise CalibrationError(
            f"Expected {len(press_labels)} presses for {word}, got {len(presses)}. "
            "Please try again."
        )
    if len(gaps) != len(gap_labels):
        raise CalibrationError("Some gaps are missing. Please try again.")

    # Every measurement, converted to "log of one unit": a 330ms dash is
    # 3 units, so it says one unit is 110ms.
    def per_unit(ms: float, kind: str) -> float:
        return math.log(max(ms, 1.0) / TEXTBOOK_UNITS[kind])

    press_kind = {".": "dot", "-": "dash"}
    samples = {"dot": [], "dash": [], "intra": [], "char": []}
    for ms, label in zip(presses, press_labels, strict=True):
        samples[press_kind[label]].append(per_unit(ms, press_kind[label]))
    for ms, label in zip(gaps, gap_labels, strict=True):
        samples[label].append(per_unit(ms, label))

    def mean(values: list[float]) -> float:
        return sum(values) / len(values)

    # Layer 1: speed, from all 14 presses. Plenty of data, used as-is.
    press_unit = mean(samples["dot"] + samples["dash"])
    # Layer 2: how much longer or shorter this person's gaps are than their
    # presses (slow to let go of the switch, say), from all 13 gaps. Also
    # plenty of data, used as-is.
    gap_unit = mean(samples["intra"] + samples["char"])
    # Layer 3: differences between kinds *within* presses (dash not 3x a dot)
    # or within gaps. Only 4-9 measurements each, so these noisy estimates are
    # pulled part-way back toward "no difference" (shrinkage): 4 measurements
    # are trusted 4/(4+3), 9 measurements 9/(9+3). A real difference still
    # shows through; a random one mostly doesn't.
    timing = {}
    for kind, group_unit in (("dot", press_unit), ("dash", press_unit),
                             ("intra", gap_unit), ("char", gap_unit)):
        n = len(samples[kind])
        difference = (mean(samples[kind]) - group_unit) * n / (n + SHRINK_STRENGTH)
        timing[kind] = math.exp(group_unit + difference) * TEXTBOOK_UNITS[kind]

    # Word gaps: assume they're stretched the same way character gaps are.
    timing["word"] = timing["char"] * TEXTBOOK_UNITS["word"] / TEXTBOOK_UNITS["char"]

    if timing["dash"] <= timing["dot"] * 1.2:
        raise CalibrationError(
            "Dashes and dots came out about the same length. "
            "Try holding the dashes a little longer."
        )
    return timing
