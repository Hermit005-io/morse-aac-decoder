"""Decoding sound as it arrives: the logic behind both `listen.py` and the
app's Listen button, with no microphone code in it.

Whoever owns the microphone hands over sound a chunk at a time:

    listener = SoundListener(sample_rate, decoder)
    listener.feed(chunk)        # as often as chunks arrive
    listener.pitch              # None until beeps have been heard

It works in two stages:
  1. Waiting. It keeps the last few seconds of sound and, twice a second,
     checks whether they contain beeps. Until they do, nothing is decoded, so
     a room with no beeps in it produces no text.
  2. Decoding. Once beeps are found, their pitch is fixed, the sound kept
     from stage 1 is decoded (it holds the start of the message), and
     everything after is decoded as it arrives.

Time is counted in samples, not by the clock, so the timing of beeps is exact
however late the chunks are delivered.
"""

import numpy as np

from .audio import StreamingToneDetector, find_beep_pitch
from .decoder_base import BaseDecoder
from .session import LiveSession

SAMPLE_RATE = 8000        # plenty for beeps, which are below 1200 Hz
SEARCH_WINDOW_S = 3.0     # how much recent sound is searched for beeps
SEARCH_EVERY_S = 0.5      # how often


class SoundListener:
    def __init__(self, sample_rate: int, decoder: BaseDecoder, pitch: float | None = None):
        """Give `pitch` (in Hz) to skip stage 1 and decode at that pitch at once."""
        self.sample_rate = sample_rate
        self.session = LiveSession(decoder)
        self.pitch = pitch
        self._detector: StreamingToneDetector | None = None
        self._recent = np.zeros(0)
        self._unsearched = 0      # samples received since the last search
        self._samples_decoded = 0
        if pitch is not None:
            self._start_decoding(pitch)

    @property
    def hearing_beeps(self) -> bool:
        return self._detector is not None

    @property
    def pending_pattern(self) -> str:
        return self.session.decoder.pending_pattern

    def flush(self) -> None:
        """Finish the letter in progress (when listening stops, say)."""
        self.session.decoder.flush()

    def clear(self) -> None:
        """Forget the letter in progress."""
        self.session.decoder.clear()

    def feed(self, samples: np.ndarray) -> None:
        if self._detector is None:
            self._search(samples)
        else:
            self._decode(samples)

    # ---- stage 1 -------------------------------------------------------------------------
    def _search(self, samples: np.ndarray) -> None:
        window = int(SEARCH_WINDOW_S * self.sample_rate)
        self._recent = np.concatenate([self._recent, samples])[-window:]
        self._unsearched += len(samples)
        if len(self._recent) < window or self._unsearched < SEARCH_EVERY_S * self.sample_rate:
            return
        self._unsearched = 0
        pitch = find_beep_pitch(self._recent, self.sample_rate)
        if pitch is not None:
            self.pitch = pitch
            self._start_decoding(pitch)
            kept, self._recent = self._recent, np.zeros(0)
            self._decode(kept)

    # ---- stage 2 -------------------------------------------------------------------------
    def _start_decoding(self, pitch: float) -> None:
        self._detector = StreamingToneDetector(self.sample_rate, pitch, self._on_edge)

    def _on_edge(self, is_on: bool, t_ms: float) -> None:
        if is_on:
            self.session.press_down(t_ms)
        else:
            self.session.press_up(t_ms)

    def _decode(self, samples: np.ndarray) -> None:
        # In pieces of a twentieth of a second, so the session's clock (which
        # finishes letters and words after a long enough silence) advances
        # smoothly even when a large chunk arrives at once.
        piece = max(1, self.sample_rate // 20)
        for start in range(0, len(samples), piece):
            part = samples[start:start + piece]
            self._detector.feed(part)
            self._samples_decoded += len(part)
            self.session.tick(self._samples_decoded / self.sample_rate * 1000)
