"""Logic every decoder shares: collecting dots and dashes, finishing characters
and words, the delete signal, and (optionally) repairing invalid characters.

Word spaces can be typed in three ways (the `spaces` setting):
  * "timing" (the default here): a pause that is long *for this sender*
    means a new word. This is how Morse is normally sent, e.g. over radio.
    The pause is learned, so for a fast sender it can be well under a second
    (with a letter pause set, see below, never less than 2.5 times that).
  * "code": only on purpose, with the space code ..-- or one long hold. A
    pause of any length just finishes the letter, so someone can stop to
    think for as long as they need. ---- deletes.
  * "pause": everything "code" has, and also a fixed, generous pause
    (`space_pause_ms`, 2.5 seconds unless set otherwise) adds a space. Easy to
    discover and needs no extra press, at the cost that stopping to think for
    longer than that in the middle of a word adds a space too. For a very
    slow sender the pause is stretched so it's never shorter than their
    learned word gap. With a letter pause set (see below) nothing is learned
    into it: it is the fixed pause, or 2.5 letter pauses if that is longer.

    A space can also be typed as one long hold: a press much longer than this
    person's dash (LONG_HOLD_DASHES times their usual dash, and never less than
    LONG_HOLD_MIN_MS). Being a single press, it can't be split by a hesitation
    the way a four-press code can.

    People tend to hesitate where the space code switches from taps to holds
    (..|--), which can split it into the letters I and M. A hesitation is
    longer than a gap inside a letter but shorter than this person's usual
    gap between letters. So when an M follows an I across a gap in that
    range, the two are read as the space code instead. Only this split is
    rescued: the other ways ..-- can split (E+W, U+T) are common inside real
    words, like NEW and BUT, and a hesitation is unlikely there anyway.

The letter pause (`letter_pause_ms`) decides where letters end. Left at 0,
the decoder's timing model decides which silences end a letter. Set to, say,
1000, the rule is simply: a silence of one second ends the letter, and nothing
shorter does, whatever the timing model thinks. This is for people who are
still finding their way around the code. Someone working out what to press
next leaves gaps that come from thinking, not from rhythm; a decoder that
reads those gaps as rhythm ends the letter before they can carry on. A fixed
pause is also something a person can be told, and can count. A pause that adds
a space is then at least LETTER_PAUSES_PER_SPACE letter pauses long, so there
is always time to start the next letter of a word once the last has shown.

A specific decoder only has to answer two questions:
  * classify_press(duration) -> is this press a dot or a dash, and how sure am I?
  * classify_gap(duration)   -> is this silence inside a character, between
                                characters, or between words, and how sure am I?

Everything else lives here, so the naive and adaptive decoders are compared on
their timing model alone.
"""

import math
from typing import NamedTuple

from .events import Event
from .table import DELETE_CODE, SPACE_CODE, decode_pattern, is_error_signal

INTRA, CHAR, WORD = "intra", "char", "word"

# A press this many times longer than the sender's usual dash is a "long hold".
LONG_HOLD_DASHES = 2.5
# ...but never shorter than this. Someone who calibrated with quick taps may
# relax into slower dashes when really typing, before the decoder has caught
# up; a hold under about half a second is too easy to do by accident.
LONG_HOLD_MIN_MS = 600.0

# In "pause" mode, hands off the switch for this long adds a space.
DEFAULT_SPACE_PAUSE_MS = 2500.0
# With a letter pause set, a pause must be at least this many letter pauses
# long to add a space. (Textbook timing has the same proportion: a silence
# ends a letter after 2 units and a word after 5.)
LETTER_PAUSES_PER_SPACE = 2.5


SPACE_MODES = ("timing", "code", "pause")


class DecodedChar(NamedTuple):
    char: str          # the decoded character, ' ' for a word break, '?' if unknown
    pattern: str       # the dots and dashes it came from (after any repair)
    confidence: float  # 0.0 (a guess) .. 1.0 (clear-cut)
    repaired: bool = False


class BaseDecoder:
    def __init__(self, repair: bool = True, spaces: str = "timing",
                 space_pause_ms: float = DEFAULT_SPACE_PAUSE_MS,
                 letter_pause_ms: float = 0.0):
        if spaces not in SPACE_MODES:
            raise ValueError(f"spaces must be one of {SPACE_MODES}")
        self.repair = repair
        self.spaces = spaces
        self.space_pause_ms = space_pause_ms
        self.letter_pause_ms = letter_pause_ms
        self.output: list[DecodedChar] = []
        self._symbols: list[str] = []
        self._symbol_conf: list[float] = []
        self._intra_gap_conf: list[float] = []  # one per gap *inside* the char
        self._short_break = False  # was the break before this char unusually short?

    # ---- to be provided by subclasses ------------------------------------
    def classify_press(self, duration_ms: float) -> tuple[str, float]:
        raise NotImplementedError

    def classify_gap(self, duration_ms: float) -> tuple[str, float]:
        raise NotImplementedError

    def char_gap_threshold_ms(self) -> float:
        """Silence longer than this ends a character (used for live timeouts)."""
        raise NotImplementedError

    def word_gap_threshold_ms(self) -> float:
        """Silence longer than this ends a word (used for live timeouts)."""
        raise NotImplementedError

    def typical_char_gap_ms(self) -> float:
        """This sender's usual gap between letters."""
        raise NotImplementedError

    def typical_dash_ms(self) -> float:
        """This sender's usual dash length."""
        raise NotImplementedError

    def letter_break_ms(self) -> float:
        """Silence longer than this ends a letter: the letter pause if one is
        set, otherwise the decoder's own threshold."""
        return self.letter_pause_ms or self.char_gap_threshold_ms()

    def long_hold_ms(self) -> float:
        """A press longer than this is a long hold (a space, in code mode)."""
        return max(LONG_HOLD_DASHES * self.typical_dash_ms(), LONG_HOLD_MIN_MS)

    @property
    def uses_codes(self) -> bool:
        """Whether the space code, delete code and long hold are active."""
        return self.spaces != "timing"

    def word_break_ms(self) -> float:
        """Silence longer than this adds a space (infinite if pauses never do)."""
        shortest = LETTER_PAUSES_PER_SPACE * self.letter_pause_ms
        if self.spaces == "timing":
            return max(self.word_gap_threshold_ms(), shortest)
        if self.spaces == "pause":
            if self.letter_pause_ms:
                # Two fixed rules the person has been told. Neither is learned.
                return max(self.space_pause_ms, shortest)
            return max(self.space_pause_ms, self.word_gap_threshold_ms())
        return math.inf

    def is_long_hold(self, duration_ms: float) -> bool:
        return self.uses_codes and duration_ms > self.long_hold_ms()

    def short_break_limit_ms(self) -> float:
        """A letter break shorter than this is on the short side for this
        sender: halfway (on a log scale) between the shortest gap that counts
        as a letter break and their usual one."""
        longest_inside = self.letter_break_ms()
        return math.sqrt(longest_inside * max(self.typical_char_gap_ms(), longest_inside))

    def learn_press(self, duration_ms: float, symbol: str) -> None:
        """Hook for decoders that adapt. The naive decoder ignores it."""

    def learn_gap(self, duration_ms: float, kind: str, overruled: bool = False) -> None:
        """Hook for decoders that adapt. The naive decoder ignores it.
        `overruled` means the letter pause decided `kind`, against the
        decoder's own reading."""

    # ---- streaming input ---------------------------------------------------
    def press(self, duration_ms: float) -> None:
        if self.is_long_hold(duration_ms):
            # A space. Finish any letter in progress first. Nothing is learned
            # from it: it isn't a dash, and would drag the dash estimate up.
            self.end_char()
            self._add_space()
            return
        symbol, conf = self.classify_press(duration_ms)
        self.learn_press(duration_ms, symbol)
        self._symbols.append(symbol)
        self._symbol_conf.append(conf)

    def gap(self, duration_ms: float) -> None:
        kind, conf, overruled = self._read_gap(duration_ms)
        # Judged before learning from this gap, which would move the yardstick.
        short = kind != INTRA and duration_ms < self.short_break_limit_ms()
        if self.spaces == "timing":
            ends_word = (kind == WORD
                         and duration_ms > LETTER_PAUSES_PER_SPACE * self.letter_pause_ms)
        else:
            ends_word = duration_ms > self.word_break_ms()
        self.learn_gap(duration_ms, kind, overruled)
        if kind == INTRA:
            if self._symbols:
                self._intra_gap_conf.append(conf)
        elif ends_word:
            self.end_word()
            self._short_break = False
        else:
            self.end_char()
            self._short_break = short

    def _read_gap(self, duration_ms: float) -> tuple[str, float, bool]:
        """What kind of gap this is, how sure, and whether the letter pause
        overruled the decoder's own reading to get there."""
        kind, conf = self.classify_gap(duration_ms)
        if not self.letter_pause_ms:
            return kind, conf, False
        # The letter pause decides, not the timing model. Half or double the
        # pause is clear-cut; right at it is a coin flip.
        inside = duration_ms < self.letter_pause_ms
        overruled = inside != (kind == INTRA)
        if inside:
            kind = INTRA
        elif kind == INTRA:
            kind = CHAR
        miss = abs(math.log(max(duration_ms, 1.0) / self.letter_pause_ms))
        return kind, min(1.0, miss / math.log(2)), overruled

    def late_gap(self, duration_ms: float) -> None:
        """A gap whose letter was already finished early, by a live session's
        clock. There's nothing left to end, but the decoder still learns from
        it, and notes whether this was an unusually short letter break."""
        kind, _, overruled = self._read_gap(duration_ms)
        self._short_break = duration_ms < self.short_break_limit_ms()  # before learning
        self.learn_gap(duration_ms, kind, overruled)

    def feed(self, events: list[Event]) -> None:
        for event in events:
            if event.is_on:
                self.press(event.duration_ms)
            else:
                self.gap(event.duration_ms)

    def end_char(self) -> None:
        if not self._symbols:
            return
        pattern = "".join(self._symbols)
        if is_error_signal(pattern) or (self.uses_codes and pattern == DELETE_CODE):
            self.delete_last()
        elif self.uses_codes and pattern == SPACE_CODE:
            self._add_space()
        elif self._is_split_space_code(pattern):
            self.output.pop()  # the I
            self._add_space()
        else:
            self.output.extend(self._resolve(pattern))
        self._symbols.clear()
        self._symbol_conf.clear()
        self._intra_gap_conf.clear()

    def end_word(self) -> None:
        self.end_char()
        if self.spaces != "code":
            self._add_space()

    def _is_split_space_code(self, pattern: str) -> bool:
        if not self.uses_codes or pattern != "--" or not self.output:
            return False
        previous = self.output[-1]
        return previous.char == "I" and previous.pattern == ".." and self._short_break

    def _add_space(self) -> None:
        if self.output and self.output[-1].char != " ":
            self.output.append(DecodedChar(" ", "", 1.0))

    def flush(self) -> None:
        """Finish the letter in progress, if any, without waiting for a gap."""
        self.end_char()

    def finish(self) -> None:
        """Call when input is over: flush the last letter, drop spaces after it."""
        self.flush()
        while self.output and self.output[-1].char == " ":
            self.output.pop()

    @property
    def pending_pattern(self) -> str:
        return "".join(self._symbols)

    @property
    def text(self) -> str:
        return "".join(c.char for c in self.output)

    def decode(self, events: list[Event]) -> str:
        """Convenience for batch use: feed everything, flush, return text."""
        self.feed(events)
        self.finish()
        return self.text

    def delete_last(self) -> None:
        """Remove the last character (and any space after it)."""
        while self.output and self.output[-1].char == " ":
            self.output.pop()
        if self.output:
            self.output.pop()

    def clear(self) -> None:
        self._short_break = False
        self.output.clear()
        self._symbols.clear()
        self._symbol_conf.clear()
        self._intra_gap_conf.clear()

    # ---- internals -----------------------------------------------------------

    def _resolve(self, pattern: str) -> list[DecodedChar]:
        confidence = min(self._symbol_conf + self._intra_gap_conf)
        char = decode_pattern(pattern)
        if char is not None:
            return [DecodedChar(char, pattern, confidence)]
        if self.repair:
            repaired = self._try_repair(pattern)
            if repaired:
                return repaired
        return [DecodedChar("?", pattern, 0.0)]

    def _try_repair(self, pattern: str) -> list[DecodedChar] | None:
        """The pattern isn't valid Morse, so something was misread. Try the
        single fix the decoder was least sure about, then the next, and so on:

          * flip one dot/dash (a press that was near the dot/dash boundary), or
          * split into two characters at one gap (a gap that was near the
            intra/char boundary, so a character break was probably missed).
            Not with a letter pause set: nothing shorter than the pause ends
            a letter then, and that includes here.
        """
        candidates = [(conf, "flip", i) for i, conf in enumerate(self._symbol_conf)]
        if not self.letter_pause_ms:
            candidates += [(conf, "split", i) for i, conf in enumerate(self._intra_gap_conf)]
        candidates.sort()
        for _conf, kind, i in candidates:
            if kind == "flip":
                flipped = pattern[:i] + ("-" if pattern[i] == "." else ".") + pattern[i + 1:]
                char = decode_pattern(flipped)
                if char is not None:
                    return [DecodedChar(char, flipped, 0.25, repaired=True)]
            else:
                left, right = pattern[: i + 1], pattern[i + 1:]
                a, b = decode_pattern(left), decode_pattern(right)
                if a is not None and b is not None:
                    return [
                        DecodedChar(a, left, 0.25, repaired=True),
                        DecodedChar(b, right, 0.25, repaired=True),
                    ]
        return None
