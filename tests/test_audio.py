import numpy as np
import pytest

from morse_aac.adaptive import AdaptiveDecoder
from morse_aac.audio import (
    StreamingToneDetector,
    detect_pitch,
    events_from_audio,
    find_beep_pitch,
    goertzel_power,
    read_wav,
    synthesize_audio,
    write_wav,
)
from morse_aac.events import Event
from morse_aac.metrics import character_error_rate
from morse_aac.room import SCENARIOS, add_room, room_noise
from morse_aac.session import LiveSession
from morse_aac.synth import PHRASES, SenderProfile, synthesize

RATE = 8000
SENDER = SenderProfile(unit_ms=100, press_jitter=0.1, gap_jitter=0.1)


def test_goertzel_matches_the_fft_bin():
    rng = np.random.default_rng(0)
    frame = rng.normal(size=(1, 80))
    k = 7  # bin 7 of 80 at 8000 Hz = 700 Hz
    fft_power = abs(np.fft.fft(frame[0])[k]) ** 2 / 80 ** 2
    assert goertzel_power(frame, RATE, 700.0)[0] == pytest.approx(fft_power, rel=1e-9)


def test_pitch_detection():
    t = np.arange(RATE) / RATE
    assert detect_pitch(np.sin(2 * np.pi * 640 * t), RATE) == pytest.approx(640, abs=2)


@pytest.mark.parametrize("noise", [0.0, 0.5, 1.0])
def test_recording_decodes_through_noise(noise):
    events = synthesize("I NEED WATER", SENDER, seed=1)
    audio = synthesize_audio(events, RATE, 650, noise_level=noise)
    found, pitch = events_from_audio(audio, RATE)
    assert pitch == pytest.approx(650, abs=5)
    assert AdaptiveDecoder().decode(found) == "I NEED WATER"


def test_wav_round_trip(tmp_path):
    # Fixed seed: every test run uses the same timing, so results are repeatable.
    audio = synthesize_audio(synthesize("SOS", SENDER, seed=0), RATE, 700)
    path = str(tmp_path / "sos.wav")
    write_wav(path, audio, RATE)
    samples, rate = read_wav(path)
    assert rate == RATE
    assert AdaptiveDecoder().decode(events_from_audio(samples, rate)[0]) == "SOS"


@pytest.mark.parametrize("noise", [0.0, 0.5, 1.0])
def test_streaming_microphone_path(noise):
    events = synthesize("PLEASE CALL MY NURSE", SENDER, seed=3)
    audio = synthesize_audio(events, RATE, 650, noise_level=noise)
    session = LiveSession(AdaptiveDecoder())

    def on_edge(is_on, t_ms):
        (session.press_down if is_on else session.press_up)(t_ms)

    detector = StreamingToneDetector(RATE, 650, on_edge)
    for start in range(0, len(audio), 400):  # 50ms chunks, like a microphone
        detector.feed(audio[start:start + 400])
        session.tick((start + 400) / RATE * 1000)
    session.tick(len(audio) / RATE * 1000 + 5000)
    session.decoder.finish()
    assert session.decoder.text == "PLEASE CALL MY NURSE"


# ---- in a (simulated) room -------------------------------------------------------------
# The detector once passed everything above and then turned a real room's
# background noise into a stream of letters. These tests are the ones that
# would have caught it. See morse_aac/room.py for what the simulation is.
ROOM_SENDER = SenderProfile(unit_ms=120, press_jitter=0.15, gap_jitter=0.15)
LOUD_TALKING = SCENARIOS["talking louder than beeps"]


def decode_recording(audio, pitch=700.0):
    return AdaptiveDecoder().decode(events_from_audio(audio, RATE, pitch)[0])


def test_room_noise_alone_is_not_decoded_as_letters():
    letters = 0
    for seed in range(3):
        noise = room_noise(20 * RATE, RATE, np.random.default_rng(seed), **LOUD_TALKING)
        letters += len(decode_recording(noise).replace(" ", ""))
    assert letters <= 1  # in a minute of loud talking, clicks and rumble


# Most letters wrong allowed, averaged over six phrases. Without any audio at
# all, this sender's timing wobble alone costs the decoder about 3%.
ROOM_LIMITS = {
    "quiet room": 0.05,
    "quiet room, typing": 0.05,
    "fan and quiet talking": 0.05,
    "talking as loud as beeps": 0.05,
    "talking louder than beeps": 0.15,
    # The two echo scenarios are a known weak spot (see the README) and are
    # reported by room_report.py rather than asserted here.
}


@pytest.mark.parametrize("scenario", ROOM_LIMITS)
def test_beeps_decode_in_a_noisy_room(scenario):
    errors = []
    for i, phrase in enumerate(PHRASES[:6]):
        clean = synthesize_audio(synthesize(phrase, ROOM_SENDER, seed=i), RATE, 700)
        audio = add_room(clean, RATE, np.random.default_rng(i), **SCENARIOS[scenario])
        errors.append(character_error_rate(phrase, decode_recording(audio)))
    assert np.mean(errors) < ROOM_LIMITS[scenario]


def test_the_echo_after_a_beep_is_not_a_second_beep():
    events = synthesize("SOS", SenderProfile(unit_ms=120))
    audio = add_room(synthesize_audio(events, RATE, 700), RATE, np.random.default_rng(0),
                     echo=0.05, level=0.004, voices=False, clicks=False)
    found, _ = events_from_audio(audio, RATE, 700.0)
    assert sum(e.is_on for e in found) == 9


def test_pitch_is_found_among_voices_and_only_when_there_are_beeps():
    rng = np.random.default_rng(0)
    noise = room_noise(3 * RATE, RATE, rng, **LOUD_TALKING)
    assert find_beep_pitch(noise, RATE) is None

    clean = synthesize_audio(synthesize("I NEED WATER", ROOM_SENDER, seed=0), RATE, 555)
    audio = add_room(clean, RATE, rng, lead_s=0.2, **LOUD_TALKING)
    assert find_beep_pitch(audio[: 3 * RATE], RATE) == pytest.approx(555, abs=10)


def test_a_much_quieter_stray_tone_after_a_message_is_ignored():
    def heard(stray_gain, wait_s):
        message = synthesize_audio(synthesize("SOS", SenderProfile(unit_ms=120)), RATE, 700,
                                   lead_in_ms=300)
        silence = np.zeros(int(wait_s * RATE))
        stray = stray_gain * synthesize_audio([Event(True, 120)], RATE, 700, lead_in_ms=300)
        rng = np.random.default_rng(0)
        audio = np.concatenate([message, silence, stray])
        audio = audio + room_noise(len(audio), RATE, rng, level=0.002, voices=False, clicks=False)
        return AdaptiveDecoder().decode(events_from_audio(audio, RATE, 700.0)[0])

    assert heard(stray_gain=0.05, wait_s=3) == "SOS"      # 26 dB quieter: ignored
    assert heard(stray_gain=1.0, wait_s=3) == "SOS E"     # as loud as the beeps: heard
    assert heard(stray_gain=0.05, wait_s=60) == "SOS E"   # a quiet sender, much later: heard
