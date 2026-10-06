"""Morse by knocking: taps on a desk, a table, a bed rail.

A beep has a length, and the length is what makes it a dot or a dash. A knock
has none: every knock sounds the same however long the finger stays down. So
plain Morse cannot be knocked; some extra rule is needed to say which knocks
are dashes. The rule here:

    one tap                      a dot
    two taps close together      a dash   (within DOUBLE_TAP_MS of each other)

The rest works the way typing does: stop for the letter pause and the letter
is finished; stop for 2.5 seconds and a space is added.

Three pieces, each usable on its own:

    KnockDetector   sound in, "a knock at time t" out
    TapReader       knocks in, dots and dashes into a TapDecoder
    TapListener     the two joined, for whoever owns the microphone

WHAT COUNTS AS A KNOCK
----------------------
A sound that jumps well above the background and has died away again a tenth
of a second later. The second half matters as much as the first: a voice also
starts suddenly, but it then carries on, and a knock does not. Anything short
and sharp does count (a pen put down, a key on a keyboard), so tap listening
is for a reasonably quiet desk. Once real knocks have been heard, much quieter
ones are ignored, as with beeps (see audio.py).
"""

import math

import numpy as np

from .decoder_base import DEFAULT_SPACE_PAUSE_MS, LETTER_PAUSES_PER_SPACE, BaseDecoder
from .table import encode

# Two taps this close together are one dash. People double-click a mouse about
# 100 to 250 ms apart, so this leaves room; separate taps need a clear beat
# between them.
DOUBLE_TAP_MS = 300.0
# With no other setting to go by, a letter ends after this long without a tap.
DEFAULT_LETTER_PAUSE_MS = 1000.0

HOP_MS = 5.0                # loudness is measured this often
# A knock must rise this far above where the loudness has recently been...
ONSET_RISE_DB = 15.0
# ...and fall this far back from its loudest point within this long.
DECAY_DROP_DB = 12.0
DECAY_WITHIN_MS = 100.0
# How fast "where the loudness has recently been" may creep up (it follows
# the loudness down at once).
FLOOR_RISE_DB_PER_S = 20.0
# A new knock must not be far quieter than the ones before. The bar relaxes
# slowly, so someone tapping more softly is heard again after a while.
QUIET_DROP_DB = 12.0
QUIET_FADE_DB_PER_S = 0.5
# Low rumble (traffic, a fan, the desk itself humming) is turned down before
# measuring, by subtracting most of the previous sample from each sample.
PRE_EMPHASIS = 0.95


class KnockDetector:
    """Feed it sound a chunk at a time; it calls on_knock(t_ms) for each knock,
    with t_ms the moment the knock began, counted from the first sample.

    A knock is only reported once it has died away, so on_knock can come a
    little over DECAY_WITHIN_MS after the knock itself."""

    def __init__(self, sample_rate: int, on_knock):
        self.sample_rate = sample_rate
        self.on_knock = on_knock
        self.hop = max(1, int(sample_rate * HOP_MS / 1000))
        self.hop_ms = self.hop / sample_rate * 1000
        self._buffer = np.zeros(0)
        self._last_sample = 0.0
        self._frames_seen = 0
        self._floor_db: float | None = None
        self._rising_since: int | None = None   # frame a possible knock began
        self._peak_db = 0.0
        self._bar_db = -math.inf                 # see QUIET_DROP_DB
        self._bar_frame = 0

    def feed(self, samples: np.ndarray) -> None:
        samples = samples.astype(np.float64)
        if len(samples) == 0:
            return
        sharpened = samples - PRE_EMPHASIS * np.concatenate([[self._last_sample], samples[:-1]])
        self._last_sample = float(samples[-1])
        self._buffer = np.concatenate([self._buffer, sharpened])
        count = len(self._buffer) // self.hop
        frames = self._buffer[: count * self.hop].reshape(count, self.hop)
        self._buffer = self._buffer[count * self.hop:]
        for power in (frames * frames).mean(axis=1):
            self._frame(10 * math.log10(power + 1e-12))

    def _frame(self, loudness_db: float) -> None:
        self._frames_seen += 1
        if self._floor_db is None:
            self._floor_db = loudness_db

        if self._rising_since is None:
            if loudness_db > self._floor_db + ONSET_RISE_DB:
                self._rising_since = self._frames_seen - 1
                self._peak_db = loudness_db
            else:
                rise = FLOOR_RISE_DB_PER_S * self.hop_ms / 1000
                self._floor_db = min(loudness_db, self._floor_db + rise)
            return

        self._peak_db = max(self._peak_db, loudness_db)
        if loudness_db < self._peak_db - DECAY_DROP_DB:
            # Short and sharp: a knock, if it's loud enough next to the others.
            quiet_for_s = (self._frames_seen - self._bar_frame) * self.hop_ms / 1000
            bar_db = self._bar_db - QUIET_FADE_DB_PER_S * quiet_for_s
            if self._peak_db > bar_db:
                self._bar_db = max(self._peak_db - QUIET_DROP_DB, bar_db)
                self._bar_frame = self._frames_seen
                self.on_knock(self._rising_since * self.hop_ms)
        elif (self._frames_seen - self._rising_since) * self.hop_ms <= DECAY_WITHIN_MS:
            return  # still loud, but it may yet die away in time
        # Either way this sound is dealt with. Whatever is left of it (the
        # tail of the knock, or a voice carrying on) is the background now.
        self._rising_since = None
        self._floor_db = loudness_db


class TapDecoder(BaseDecoder):
    """A decoder whose dots and dashes arrive already decided, so it has no
    timing model at all: it only collects them into letters. The TapReader
    feeding it does the timing."""

    def __init__(self):
        super().__init__(repair=False, spaces="pause")

    def add(self, symbol: str) -> None:
        self._symbols.append(symbol)
        self._symbol_conf.append(1.0)


class TapReader:
    """Turns knocks into letters, as they happen.

        reader.knock(t_ms)    for each knock
        reader.tick(t_ms)     often, so letters finish when the tapping stops

    Like LiveSession, it never reads the clock itself.
    """

    def __init__(self, decoder: TapDecoder, letter_pause_ms: float = DEFAULT_LETTER_PAUSE_MS,
                 space_pause_ms: float = DEFAULT_SPACE_PAUSE_MS,
                 double_tap_ms: float = DOUBLE_TAP_MS):
        self.decoder = decoder
        self.letter_pause_ms = max(letter_pause_ms, 2 * double_tap_ms)
        # As in typing: long enough to start the next letter once one shows.
        self.space_pause_ms = max(space_pause_ms, LETTER_PAUSES_PER_SPACE * self.letter_pause_ms)
        self.double_tap_ms = double_tap_ms
        self.knocks = 0
        self._group = 0                       # taps in the dot or dash being made
        self._last_knock: float | None = None
        self._letter_done = True
        self._word_done = True

    def knock(self, t_ms: float) -> None:
        self.knocks += 1
        if self._last_knock is not None:
            self._settle(t_ms - self._last_knock)
        self._group += 1
        self._last_knock = t_ms
        self._letter_done = self._word_done = False

    def tick(self, t_ms: float) -> None:
        if self._last_knock is not None:
            self._settle(t_ms - self._last_knock)

    def _settle(self, quiet_ms: float) -> None:
        """Do whatever `quiet_ms` without a knock has made final."""
        if self._group and quiet_ms > self.double_tap_ms:
            self.decoder.add("." if self._group == 1 else "-")
            self._group = 0
        if not self._letter_done and quiet_ms > self.letter_pause_ms:
            self.decoder.end_char()
            self._letter_done = True
        if not self._word_done and quiet_ms > self.space_pause_ms:
            self.decoder.end_word()
            self._word_done = True

    def flush(self) -> None:
        """Finish the letter in progress without waiting for the pause."""
        if self._last_knock is not None and not self._letter_done:
            self._settle(self.letter_pause_ms + 1)

    def finish(self) -> None:
        """Call when the tapping is over: flush, and drop spaces at the end."""
        self.flush()
        self.decoder.finish()

    def clear(self) -> None:
        """Forget the letter in progress."""
        self._group = 0
        self._letter_done = self._word_done = True
        self.decoder.clear()

    @property
    def pending_pattern(self) -> str:
        """The letter so far, including a tap that may still become a dash."""
        making = "" if not self._group else "." if self._group == 1 else "-"
        return self.decoder.pending_pattern + making


class TapListener:
    """Sound in, text out: the tap-listening twin of listener.SoundListener."""

    def __init__(self, sample_rate: int, decoder: TapDecoder,
                 letter_pause_ms: float = DEFAULT_LETTER_PAUSE_MS):
        self.sample_rate = sample_rate
        self.reader = TapReader(decoder, letter_pause_ms)
        self._detector = KnockDetector(sample_rate, self.reader.knock)
        self._samples_heard = 0

    @property
    def knocks(self) -> int:
        return self.reader.knocks

    def feed(self, samples: np.ndarray) -> None:
        # In pieces of a twentieth of a second, so letters finish on time even
        # when a large chunk arrives at once.
        piece = max(1, self.sample_rate // 20)
        for start in range(0, len(samples), piece):
            part = samples[start:start + piece]
            self._detector.feed(part)
            self._samples_heard += len(part)
            # A knock is reported a little late (see KnockDetector), so the
            # reader's clock runs that far behind the sound: otherwise it could
            # close a dot just before hearing of the second tap of a dash.
            heard_ms = self._samples_heard / self.sample_rate * 1000
            self.reader.tick(heard_ms - DECAY_WITHIN_MS - 2 * HOP_MS)

    @property
    def pending_pattern(self) -> str:
        return self.reader.pending_pattern

    def flush(self) -> None:
        self.reader.flush()

    def finish(self) -> None:
        self.reader.finish()

    def clear(self) -> None:
        self.reader.clear()


# ---- made-up knocks, for tests and demo files ---------------------------------------
def tap_times(text: str, rng: np.random.Generator, double_ms: float = 160.0,
              beat_ms: float = 550.0, letter_ms: float = 1700.0, word_ms: float = 3600.0,
              wobble: float = 0.15) -> list[float]:
    """When someone tapping `text` by the rule above would knock, in ms.

    double_ms is the time between the two taps of a dash, beat_ms between one
    dot or dash and the next, letter_ms between letters, word_ms between
    words; each is varied by `wobble` (0.15 = typically 15% off)."""
    def about(ms: float) -> float:
        return ms * math.exp(rng.normal(0.0, wobble)) if wobble else ms

    times: list[float] = []
    t = 300.0
    for wi, word in enumerate(encode(text)):
        if wi:
            t += about(word_ms)
        for ci, pattern in enumerate(word):
            if ci:
                t += about(letter_ms)
            for si, symbol in enumerate(pattern):
                if si:
                    t += about(beat_ms)
                times.append(t)
                if symbol == "-":
                    t += about(double_ms)
                    times.append(t)
    return times


def knock_audio(times_ms: list[float], sample_rate: int, rng: np.random.Generator,
                tail_s: float = 4.0, loudness: float = 0.5) -> np.ndarray:
    """What a microphone on the desk might pick up from knocks at those times:
    each a burst of noise dying away in a few milliseconds (the click) on top
    of a low thud that rings a little longer (the desk), none exactly as loud
    as the last. A stand-in for a real desk, not a measurement of one."""
    audio = np.zeros(int((max(times_ms, default=0.0) / 1000 + tail_s) * sample_rate))
    length = int(0.15 * sample_rate)
    t = np.arange(length) / sample_rate
    for time_ms in times_ms:
        click = rng.normal(0.0, 1.0, length) * np.exp(-t / 0.006)
        thud = np.sin(2 * np.pi * rng.uniform(120.0, 220.0) * t) * np.exp(-t / 0.03)
        knock = loudness * 10 ** (rng.normal(0.0, 2.0) / 20) * (0.6 * click + 0.6 * thud)
        start = int(time_ms / 1000 * sample_rate)
        audio[start:start + length] += knock[: len(audio) - start]
    return audio
