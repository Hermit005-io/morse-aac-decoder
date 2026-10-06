"""The textbook decoder: the baseline everything is measured against.

It assumes the sender follows standard Morse timing exactly, all the time:

    dot = 1 unit     dash = 3 units
    gap inside a character = 1 unit
    gap between characters = 3 units
    gap between words      = 7 units

and it splits halfway between them: a press under 2 units is a dot, a silence
under 2 units is inside a character, under 5 units is between characters.

The unit is fixed when the decoder is created and never changes. That's the
weakness this project is about: a person who speeds up, slows down as they tire,
or whose dashes aren't exactly 3x their dots, drifts away from the fixed rules.
"""

import math

from .decoder_base import CHAR, DEFAULT_SPACE_PAUSE_MS, INTRA, WORD, BaseDecoder


def log_margin(value: float, low: float, high: float) -> float:
    """How far `value` sits from the boundary between two expected lengths,
    on a 0..1 scale.

    The boundary is the geometric midpoint sqrt(low * high). A value right on
    the boundary gets 0 (coin flip); a value at or beyond either expected
    length gets 1. Log scale is used because timing errors are proportional:
    being 50ms off matters much more for a 100ms dot than a 400ms dash.
    """
    boundary = math.sqrt(low * high)
    half_span = math.log(high / boundary)
    if half_span <= 0 or value <= 0:
        return 0.0
    return min(1.0, abs(math.log(value / boundary)) / half_span)


class NaiveDecoder(BaseDecoder):
    def __init__(self, unit_ms: float, repair: bool = False, spaces: str = "timing",
                 space_pause_ms: float = DEFAULT_SPACE_PAUSE_MS, letter_pause_ms: float = 0.0):
        super().__init__(repair=repair, spaces=spaces, space_pause_ms=space_pause_ms,
                         letter_pause_ms=letter_pause_ms)
        self.unit_ms = unit_ms

    def classify_press(self, duration_ms: float) -> tuple[str, float]:
        u = self.unit_ms
        symbol = "." if duration_ms < 2 * u else "-"
        return symbol, log_margin(duration_ms, u, 3 * u)

    def classify_gap(self, duration_ms: float) -> tuple[str, float]:
        u = self.unit_ms
        if duration_ms < 2 * u:
            return INTRA, log_margin(duration_ms, u, 3 * u)
        if duration_ms < 5 * u:
            return CHAR, min(log_margin(duration_ms, u, 3 * u),
                             log_margin(duration_ms, 3 * u, 7 * u))
        return WORD, log_margin(duration_ms, 3 * u, 7 * u)

    def char_gap_threshold_ms(self) -> float:
        return 2 * self.unit_ms

    def word_gap_threshold_ms(self) -> float:
        return 5 * self.unit_ms

    def typical_char_gap_ms(self) -> float:
        return 3 * self.unit_ms

    def typical_dash_ms(self) -> float:
        return 3 * self.unit_ms
