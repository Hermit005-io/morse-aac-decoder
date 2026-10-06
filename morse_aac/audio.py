"""Turning sound into Events: Morse as beeps from a speaker, a radio, a phone.

The pipeline:

    audio samples
      -> find the beep's pitch (the strongest steady frequency, via an FFT)
      -> every 10ms, measure how much that pitch STANDS OUT from the pitches
         around it (Goertzel filters at the pitch and at six neighbours)
      -> decide ON/OFF from that, with hysteresis and a short debounce
      -> turn the ON/OFF edges into Events

Why "stands out" rather than "is loud": a first version only measured loudness
at the beep's pitch. It passed every test with steady hiss, and then decoded
the background noise of a real room as a stream of E's and T's, because real
noise swells, and voices and keyboard clicks put energy at every pitch. A beep
is different in kind: nearly all its energy sits at one pitch. So the detector
compares the power at the beep's pitch with the average power at nearby
pitches. For any broadband sound, however loud, that ratio stays near 1 (0 dB);
for a tone it is 10 to 1000 times higher.

Why Goertzel instead of a full FFT for every frame: we only need the power at
seven known frequencies. Goertzel computes the power at exactly one frequency
with a two-line loop, at any frequency (an FFT only gives fixed bins).

Needs numpy. Live microphone input also needs the `sounddevice` package.
"""

import math
import wave
from collections import deque

import numpy as np

from .events import Event

HOP_MS = 10.0                 # a decision every 10ms...
WINDOW_HOPS = 2               # ...each from the latest 20ms of sound
SMOOTH_FRAMES = 3             # powers are averaged over this many decisions

# Where the surrounding noise is measured, relative to the beep's pitch. With
# a 20ms window a tone spreads about 100 Hz either side, so 200 Hz away is clear.
NEIGHBOUR_OFFSETS_HZ = (-400.0, -300.0, -200.0, 200.0, 300.0, 400.0)

# How far the beep's pitch must stand out from its neighbours to count as a
# tone at all. Broadband noise averages 0 dB and, after smoothing, almost
# never passes 9. Once ON, it may dip to 6 before that alone turns it OFF.
TONAL_ON_DB = 9.0
TONAL_OFF_DB = 6.0
# A beep starts when the loudness at its pitch rises this far above where it
# has recently been, and ends when it falls this far below the beep's own peak.
# These two are what stop the echo after a beep from filling the gap: an echo
# is still a clear tone, but it is quieter than the beep and only ever fading.
# (These, and the mask below, were tuned on the simulated rooms in room.py,
# using different random seeds from the ones the tests and README report.)
ONSET_RISE_DB = 4.0
RELEASE_DROP_DB = 8.0
# How fast the "where it has recently been" level may creep back up.
FLOOR_RISE_DB_PER_S = 20.0
# Right after a beep, a new one must be nearly as loud as it was: within
# MASK_DROP_DB at first, relaxing by MASK_FADE_DB_PER_S. An echo wobbles as it
# fades, and without this an upward wobble can pass for a new, short beep. The
# relaxing is far slower than any room's echo dies away, and within a second
# even a much quieter sender is heard again.
MASK_DROP_DB = 6.0
MASK_FADE_DB_PER_S = 40.0
# For much longer, a new beep must not be *far* quieter than the ones before.
# On its first trial with a real microphone the detector decoded a message
# correctly and then added one stray letter: some small sound in the room that
# happened to be tone-like at the beep's pitch. Real beeps from one source
# arrive at about the same loudness; a stray sound is usually far quieter.
# This bar relaxes slowly, so a quieter sender is heard after half a minute.
QUIET_DROP_DB = 15.0
QUIET_FADE_DB_PER_S = 0.5


# ---- the pieces ---------------------------------------------------------------------
def detect_pitch(samples: np.ndarray, sample_rate: int,
                 low_hz: float = 300.0, high_hz: float = 1200.0) -> float:
    """The pitch of the beeps: the frequency between low_hz and high_hz that
    stands out most sharply from the frequencies around it.

    "Stands out most", not "is loudest": in a real room the loudest thing in
    that range is often a voice or a hum, which is spread over many
    frequencies, while a beep is a needle at one. The spectrum is averaged
    over short stretches (Welch's method) so a steady tone builds up and
    passing sounds average out.
    """
    size = min(len(samples), 1 << int(math.log2(max(sample_rate // 8, 64))))  # about 1/8 s
    if size < 64:
        raise ValueError("Not enough audio to find a pitch.")
    step = size // 2
    count = max(1, (len(samples) - size) // step + 1)
    starts = step * np.arange(count)
    frames = samples[starts[:, None] + np.arange(size)[None, :]] * np.hanning(size)
    spectrum = (np.abs(np.fft.rfft(frames, axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(size, 1.0 / sample_rate)

    # Each frequency against the typical level of its surroundings (150 Hz
    # either side).
    reach = max(3, int(150.0 / (freqs[1] - freqs[0])))
    padded = np.pad(spectrum, reach, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * reach + 1)
    prominence = spectrum / (np.median(windows, axis=1) + 1e-18)

    band = np.flatnonzero((freqs >= low_hz) & (freqs <= high_hz))
    best = band[np.argmax(prominence[band])]
    # Refine between FFT bins: the centre of mass of the peak and its neighbours.
    near = slice(max(best - 1, 0), best + 2)
    return float(np.sum(freqs[near] * spectrum[near]) / np.sum(spectrum[near]))


def goertzel_power(frames: np.ndarray, sample_rate: int, freq: float) -> np.ndarray:
    """Power at `freq` for each row of `frames` (shape: n_frames x frame_len).

    The Goertzel recurrence, run on all frames at once:
        s[n] = x[n] + coeff * s[n-1] - s[n-2],   coeff = 2 cos(2 pi freq / rate)
    and at the end, power = s1^2 + s2^2 - coeff * s1 * s2. `freq` can be any
    frequency, not only one of an FFT's bins.
    """
    n = frames.shape[1]
    coeff = 2.0 * math.cos(2.0 * math.pi * freq / sample_rate)
    s1 = np.zeros(frames.shape[0])
    s2 = np.zeros(frames.shape[0])
    for i in range(n):
        s0 = frames[:, i] + coeff * s1 - s2
        s2, s1 = s1, s0
    return (s1 * s1 + s2 * s2 - coeff * s1 * s2) / (n * n)


# ---- whole recordings -----------------------------------------------------------------
def events_from_audio(samples: np.ndarray, sample_rate: int,
                      freq: float | None = None) -> tuple[list[Event], float]:
    """Events from a whole recording. Returns (events, pitch used).

    A recording goes through exactly the same detector as live sound, so what
    is tested on files is what runs on the microphone."""
    samples = samples.astype(np.float64)
    if freq is None:
        freq = detect_pitch(samples, sample_rate)
    edges: list[tuple[bool, float]] = []
    detector = StreamingToneDetector(sample_rate, freq, lambda on, t: edges.append((on, t)))
    detector.feed(samples)
    if edges and edges[-1][0]:  # still beeping when the recording ended
        edges.append((False, len(samples) / sample_rate * 1000))
    events = [Event(is_on, t_next - t)
              for (is_on, t), (_, t_next) in zip(edges, edges[1:], strict=False)]
    return events, freq


def find_beep_pitch(samples: np.ndarray, sample_rate: int, min_beeps: int = 3) -> float | None:
    """The pitch of the beeps in `samples`, or None if there don't seem to be
    any. detect_pitch always names *some* frequency, even in a silent room, so
    this checks the answer: at that pitch, are there actually beeps?"""
    freq = detect_pitch(samples, sample_rate)
    events, _ = events_from_audio(samples, sample_rate, freq)
    return freq if sum(e.is_on for e in events) >= min_beeps else None


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM WAV file (mono, or the first channel of stereo)."""
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("Only 16-bit WAV files are supported.")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        channels = w.getnchannels()
        rate = w.getframerate()
    return data[::channels].astype(np.float64) / 32768.0, rate


def write_wav(path: str, samples: np.ndarray, sample_rate: int) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes((clipped * 32767).astype(np.int16).tobytes())


def synthesize_audio(events: list[Event], sample_rate: int = 8000, freq: float = 700.0,
                     noise_level: float = 0.0, lead_in_ms: float = 300.0,
                     seed: int = 0) -> np.ndarray:
    """Beeps for a list of Events (for tests and demo files). Each beep has
    5ms soft edges so it doesn't click. `noise_level` adds hiss (0.3 = hiss
    at 30% of the beep's loudness)."""
    rng = np.random.default_rng(seed)
    pieces = [np.zeros(int(sample_rate * lead_in_ms / 1000))]
    for event in events:
        n = int(sample_rate * event.duration_ms / 1000)
        if event.is_on:
            t = np.arange(n) / sample_rate
            tone = 0.5 * np.sin(2 * np.pi * freq * t)
            ramp = min(n // 2, int(sample_rate * 0.005))
            if ramp:
                edge = np.linspace(0, 1, ramp)
                tone[:ramp] *= edge
                tone[-ramp:] *= edge[::-1]
            pieces.append(tone)
        else:
            pieces.append(np.zeros(n))
    pieces.append(np.zeros(int(sample_rate * lead_in_ms / 1000)))
    audio = np.concatenate(pieces)
    if noise_level:
        audio = audio + rng.normal(0, 0.5 * noise_level, len(audio))
    return audio


# ---- the detector -------------------------------------------------------------------
class StreamingToneDetector:
    """ON/OFF detection for audio arriving a chunk at a time.

    Feed it samples; it calls on_edge(is_on, t_ms) whenever the beep turns on
    or off, with t_ms counted from the first sample (so it's exact, not
    affected by processing delays).

    Every 10ms it measures two things at the beep's pitch:
      * loudness, in decibels, and
      * how far that stands out from the neighbouring pitches ("tonal").

    A beep turns ON when the sound is tonal, its loudness has risen clearly
    above where it has recently been, and it is loud enough compared with the
    beeps before it (nearly as loud just after one; not far quieter for a
    while longer). It turns OFF when the sound stops being
    tonal, or its loudness falls clearly below the peak of this beep. See the
    constants at the top of the file for the amounts.
    """

    def __init__(self, sample_rate: int, freq: float, on_edge, hop_ms: float = HOP_MS,
                 min_frames: int = 2):
        self.sample_rate = sample_rate
        self.freq = freq
        self.on_edge = on_edge
        self.hop_ms = hop_ms
        self.hop = int(sample_rate * hop_ms / 1000)
        self.window = WINDOW_HOPS * self.hop
        self.min_frames = min_frames
        self._taper = np.hanning(self.window)
        self._neighbours = [f for f in (freq + offset for offset in NEIGHBOUR_OFFSETS_HZ)
                            if 80.0 <= f <= 0.45 * sample_rate]
        self._buffer = np.zeros(0)
        self._tone: deque[float] = deque(maxlen=SMOOTH_FRAMES)
        self._noise: deque[float] = deque(maxlen=SMOOTH_FRAMES)
        self._frames_seen = 0
        self._floor_db: float | None = None   # where the loudness has recently been
        self._peak_db = 0.0                    # loudest point of the current beep
        # How loud a new beep must be, as two bars that relax at different
        # speeds (see MASK_* and QUIET_* above), and when they were last set.
        self._mask_db = -math.inf
        self._quiet_db = -math.inf
        self._mask_frame = 0
        self._state = False
        self._candidate_count = 0

    def feed(self, samples: np.ndarray) -> None:
        self._buffer = np.concatenate([self._buffer, samples.astype(np.float64)])
        if len(self._buffer) < self.window:
            return
        count = (len(self._buffer) - self.window) // self.hop + 1
        starts = self.hop * np.arange(count)
        frames = self._buffer[starts[:, None] + np.arange(self.window)[None, :]] * self._taper
        self._buffer = self._buffer[count * self.hop:]
        tone = goertzel_power(frames, self.sample_rate, self.freq)
        noise = sum(goertzel_power(frames, self.sample_rate, f) for f in self._neighbours)
        noise = noise / len(self._neighbours)
        for tone_power, noise_power in zip(tone, noise, strict=True):
            self._tone.append(tone_power)
            self._noise.append(noise_power)
            tone_power = sum(self._tone) / len(self._tone) + 1e-12
            noise_power = sum(self._noise) / len(self._noise) + 1e-12
            self._frame(10 * math.log10(tone_power), 10 * math.log10(tone_power / noise_power))

    def _bars_now(self) -> tuple[float, float]:
        elapsed_s = (self._frames_seen - self._mask_frame) * self.hop_ms / 1000
        return (self._mask_db - MASK_FADE_DB_PER_S * elapsed_s,
                self._quiet_db - QUIET_FADE_DB_PER_S * elapsed_s)

    def _frame(self, loudness_db: float, tonal_db: float) -> None:
        self._frames_seen += 1
        if self._floor_db is None:
            self._floor_db = loudness_db

        if self._state:
            self._peak_db = max(self._peak_db, loudness_db)
            want = (tonal_db > TONAL_OFF_DB
                    and loudness_db > self._peak_db - RELEASE_DROP_DB)
        else:
            want = (tonal_db > TONAL_ON_DB
                    and loudness_db > self._floor_db + ONSET_RISE_DB
                    and loudness_db > max(self._bars_now()))
            if not want:
                # Follow the loudness down at once (a fading echo), up slowly.
                rise = FLOOR_RISE_DB_PER_S * self.hop_ms / 1000
                self._floor_db = min(loudness_db, self._floor_db + rise)

        # Only switch after `min_frames` decisions in a row agree (debounce).
        if want == self._state:
            self._candidate_count = 0
            return
        self._candidate_count += 1
        if self._candidate_count >= self.min_frames:
            self._state = want
            self._candidate_count = 0
            if want:
                self._peak_db = loudness_db
            else:
                self._floor_db = loudness_db
                # A quiet blip must not lower the bar set by a real beep just
                # before it, or one false beep in an echo lets in the next.
                mask_db, quiet_db = self._bars_now()
                self._mask_db = max(self._peak_db - MASK_DROP_DB, mask_db)
                self._quiet_db = max(self._peak_db - QUIET_DROP_DB, quiet_db)
                self._mask_frame = self._frames_seen
            # Debouncing delays the edge; date it back to when it happened.
            edge_frame = self._frames_seen - self.min_frames
            self.on_edge(want, max(edge_frame, 0) * self.hop_ms)
