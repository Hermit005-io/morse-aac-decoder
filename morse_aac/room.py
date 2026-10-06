"""A rough simulated room, for testing the tone detector on something harder
than clean beeps: quieter beeps, echo, and uneven background noise.

This exists because of a real failure. The detector's first version passed
every test with steady hiss added, then decoded the background noise of a real
room as a stream of letters. Steady hiss is the one kind of noise that is easy.

It is a simulation, not a room. It has:
  * rumble whose loudness swells and fades,
  * "voices": short bursts of harmonics of a gliding pitch,
  * clicks, like typing or something being put down on a desk,
  * echo: each beep is followed by a decaying, wobbling tail.
It does not have real speech, music, other beeps, or a microphone's own
quirks. Passing these tests means the detector survives this, no more.

Needs numpy.
"""

import numpy as np


def room_noise(length: int, sample_rate: int, rng: np.random.Generator, level: float = 0.01,
               voices: bool = True, clicks: bool = True,
               voice_amp: tuple[float, float] = (0.01, 0.04)) -> np.ndarray:
    """`length` samples of background noise. `level` sets the rumble and hiss;
    `voice_amp` is the range of loudness of the voice bursts. For scale, a
    beep at the microphone has amplitude 0.5 x the `beep_gain` given to add_room."""
    if length == 0:
        return np.zeros(0)
    seconds = length / sample_rate
    t = np.arange(length) / sample_rate
    white = rng.normal(0, 1, length)

    rumble = np.convolve(white, np.ones(8) / 8, mode="same")
    knots = np.arange(0, seconds + 2, 0.5)
    swell = np.exp(np.interp(t, knots, rng.normal(0, 0.6, len(knots))))
    out = level * rumble * swell * 3 + level * 0.3 * white

    if voices:
        for start in np.arange(0.3, seconds, 1.1) + rng.uniform(0, 0.5):
            duration = rng.uniform(0.15, 0.45)
            i0, i1 = int(start * sample_rate), min(length, int((start + duration) * sample_rate))
            if i1 <= i0:
                continue
            tt = np.arange(i1 - i0) / sample_rate
            pitch = rng.uniform(100, 220) * (1 + 0.2 * tt / duration)
            phase = 2 * np.pi * np.cumsum(pitch) / sample_rate
            burst = sum(np.sin(h * phase) / h for h in range(1, 12))
            out[i0:i1] += rng.uniform(*voice_amp) / 3 * burst * np.hanning(i1 - i0)

    if clicks:
        for start in rng.uniform(0, seconds, int(seconds * 2)):
            i0 = int(start * sample_rate)
            i1 = min(length, i0 + int(0.012 * sample_rate))
            fade = np.exp(-np.arange(i1 - i0) / (0.003 * sample_rate))
            out[i0:i1] += level * 15 * rng.normal(0, 1, i1 - i0) * fade
    return out


def add_room(clean: np.ndarray, sample_rate: int, rng: np.random.Generator,
             beep_gain: float = 0.25, echo: float = 0.03, lead_s: float = 1.0,
             tail_s: float = 3.0, **noise_settings) -> np.ndarray:
    """Put clean beeps (from audio.synthesize_audio) in the room.

    beep_gain: how loud the beeps arrive (1.0 = as generated).
    echo:      how strong the echo is. 0.03 is a speaker close to the
               microphone; 0.10 is across an echoey room, where the echo
               carries more energy than the direct sound.
    Remaining settings go to room_noise.
    """
    tail = int(0.25 * sample_rate)
    response = rng.normal(0, 1, tail) * np.exp(-np.arange(tail) / (0.05 * sample_rate)) * echo
    response[0] = 1.0
    heard = np.convolve(clean, response)[: len(clean)] * beep_gain
    heard = np.concatenate([np.zeros(int(lead_s * sample_rate)), heard,
                            np.zeros(int(tail_s * sample_rate))])
    return heard + room_noise(len(heard), sample_rate, rng, **noise_settings)


# Named situations, used by the tests and by room_report.py.
SCENARIOS = {
    "quiet room":                dict(level=0.004, voices=False, clicks=False),
    "quiet room, typing":        dict(level=0.004, voices=False),
    "fan and quiet talking":     dict(level=0.01, voice_amp=(0.01, 0.04)),
    "talking as loud as beeps":  dict(level=0.01, voice_amp=(0.06, 0.12)),
    "talking louder than beeps": dict(level=0.02, voice_amp=(0.12, 0.36)),
    "faint beeps, some echo":    dict(level=0.004, voices=False, beep_gain=0.03, echo=0.05),
    "echoey room":               dict(level=0.004, voices=False, clicks=False, echo=0.10),
}
