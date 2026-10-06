"""Live decoding: turning "switch went down / came up at time t" into text as it
happens.

Batch decoding (the benchmark) knows every gap's length up front. Live, a gap's
length isn't known until the next press, and nobody wants to press again just
to see their last letter. So a LiveSession also watches the clock: once the
switch has been up longer than the person's character gap (or the decoder's
letter pause, if it has one), the letter is finished and shown; longer than
their word gap, a space is added.

The session never reads the clock itself. Every call passes the time in
milliseconds, so the same code runs from the app (real time), the microphone
(sample counts), and the tests (made-up times).
"""

from dataclasses import dataclass

from .decoder_base import BaseDecoder

# With no timing model yet, how long to wait before decoding what we have.
BOOTSTRAP_IDLE_MS = 1500.0


@dataclass
class SessionState:
    text: str
    pending_pattern: str       # dots/dashes of the letter in progress
    holding_symbol: str        # what the current hold would be: '.', '-' or 'space';
                               # '' if not holding


class LiveSession:
    def __init__(self, decoder: BaseDecoder):
        self.decoder = decoder
        self._pressed_at: float | None = None
        self._released_at: float | None = None
        self._char_done = False
        self._word_done = False

    @property
    def is_pressed(self) -> bool:
        return self._pressed_at is not None

    def press_down(self, t_ms: float) -> None:
        if self._pressed_at is not None:
            return  # already down (e.g. key auto-repeat)
        if self._released_at is not None:
            gap = t_ms - self._released_at
            if self._char_done:
                # The letter was already finished by the clock; the decoder
                # still learns from how long this gap turned out to be.
                if getattr(self.decoder, "initialized", True):
                    self.decoder.late_gap(gap)
            else:
                self.decoder.gap(gap)
        self._pressed_at = t_ms

    def press_up(self, t_ms: float) -> None:
        if self._pressed_at is None:
            return
        self.decoder.press(max(t_ms - self._pressed_at, 1.0))
        self._pressed_at = None
        self._released_at = t_ms
        self._char_done = False
        self._word_done = False

    def tick(self, t_ms: float) -> bool:
        """Call often (every ~20ms). Returns True if the text changed."""
        if self._pressed_at is not None or self._released_at is None:
            return False
        idle = t_ms - self._released_at
        decoder = self.decoder
        changed = False

        if not getattr(decoder, "initialized", True):
            # With a letter pause set, the first letter keeps to it as well.
            if idle < (decoder.letter_pause_ms or BOOTSTRAP_IDLE_MS):
                return False
            decoder.force_init()
            changed = True

        if not self._char_done and idle > decoder.letter_break_ms():
            before = decoder.text
            decoder.end_char()
            self._char_done = True
            changed = changed or decoder.text != before
        if not self._word_done and idle > decoder.word_break_ms():
            before = decoder.text
            decoder.end_word()
            self._char_done = self._word_done = True
            changed = changed or decoder.text != before
        return changed

    def state(self, t_ms: float) -> SessionState:
        holding = ""
        if self._pressed_at is not None and getattr(self.decoder, "initialized", True):
            held = t_ms - self._pressed_at
            if self.decoder.is_long_hold(held):
                holding = "space"
            else:
                holding, _ = self.decoder.classify_press(held)
        return SessionState(self.decoder.text, self.decoder.pending_pattern, holding)
