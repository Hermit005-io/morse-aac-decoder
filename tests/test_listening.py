"""Listening to sound, without a microphone: the sound is handed over in
chunks, the way the app and listen.py hand over what the microphone hears."""

import numpy as np

from morse_aac.adaptive import AdaptiveDecoder
from morse_aac.audio import synthesize_audio
from morse_aac.controller import Controller
from morse_aac.listener import SAMPLE_RATE, SoundListener
from morse_aac.room import SCENARIOS, add_room, room_noise
from morse_aac.synth import SenderProfile, synthesize

SENDER = SenderProfile(unit_ms=120, press_jitter=0.1, gap_jitter=0.1)
TALKING = SCENARIOS["talking as loud as beeps"]


def beeps_in_a_room(text, pitch=640, seed=0, noise_first_s=0.0):
    rng = np.random.default_rng(seed)
    clean = synthesize_audio(synthesize(text, SENDER, seed=seed), SAMPLE_RATE, pitch)
    before = room_noise(int(noise_first_s * SAMPLE_RATE), SAMPLE_RATE, rng, **TALKING)
    return np.concatenate([before, add_room(clean, SAMPLE_RATE, rng, tail_s=4.0, **TALKING)])


def feed_in_chunks(target, audio, chunk=400):
    for start in range(0, len(audio), chunk):
        target(audio[start:start + chunk])


def test_listener_waits_through_noise_then_finds_the_pitch_and_decodes():
    decoder = AdaptiveDecoder()
    listener = SoundListener(SAMPLE_RATE, decoder)
    audio = beeps_in_a_room("I NEED WATER", noise_first_s=8.0)

    feed_in_chunks(listener.feed, audio[: 8 * SAMPLE_RATE])
    assert not listener.hearing_beeps and decoder.text == ""   # only noise so far

    feed_in_chunks(listener.feed, audio[8 * SAMPLE_RATE:])
    assert abs(listener.pitch - 640) < 10
    assert decoder.text.strip() == "I NEED WATER"


def test_listener_result_does_not_depend_on_chunk_size():
    audio = beeps_in_a_room("PLEASE WAIT")
    texts = []
    for chunk in (160, 400, 4000):
        decoder = AdaptiveDecoder()
        feed_in_chunks(SoundListener(SAMPLE_RATE, decoder).feed, audio, chunk)
        texts.append(decoder.text.strip())
    assert texts == ["PLEASE WAIT"] * 3


def test_app_hears_beeps_into_the_same_text_as_typing(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    while controller.letter_pause_s:   # typed below at a steady, fluent rhythm
        controller.next_letter_pause()
    # Type "HI" with the switch.
    t = 0.0
    for event in synthesize("HI", SenderProfile(unit_ms=120)):
        if event.is_on:
            controller.press_down(t)
            controller.press_up(t + event.duration_ms)
        t += event.duration_ms
        controller.tick(t)
    controller.tick(t + 1000)
    assert controller.text == "HI"

    controller.start_listening()
    assert "no beeps" in controller.status()
    controller.press_down(t + 2000)          # the switch is ignored while listening
    controller.press_up(t + 2100)
    feed_in_chunks(controller.feed_audio, beeps_in_a_room("YO"))
    assert "Listening: beeps at" in controller.status()
    controller.stop_listening()
    assert controller.mode == "typing"
    assert controller.text == "HI YO "        # ready for whatever is typed next

    controller.clear()
    assert controller.text == ""


def type_fluently(controller, text, start):
    t = start
    for event in synthesize(text, SenderProfile(unit_ms=120)):
        if event.is_on:
            controller.press_down(t)
            controller.press_up(t + event.duration_ms)
        t += event.duration_ms
        controller.tick(t)
    controller.tick(t + 1000)
    return t + 1000


def test_listening_and_stopping_never_joins_words_together(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    while controller.letter_pause_s:
        controller.next_letter_pause()
    t = type_fluently(controller, "HI", 0.0)
    controller.tick(t + 3000)
    assert controller.text == "HI "            # the 2.5 second pause added a space

    controller.start_listening()               # ...and listening to nothing keeps it
    controller.stop_listening()
    t = type_fluently(controller, "YO", t + 5000)
    assert controller.text == "HI YO"

    controller.start_listening()               # heard words are set apart on both sides
    feed_in_chunks(controller.feed_audio, beeps_in_a_room("NO"))
    controller.stop_listening()
    type_fluently(controller, "OK", t + 60000)
    assert controller.text == "HI YO NO OK"


def test_a_half_typed_letter_goes_before_what_is_heard(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    while controller.letter_pause_s:
        controller.next_letter_pause()
    t = type_fluently(controller, "HI", 0.0)
    controller.press_down(t + 100)             # one more dot, and straight to Listen
    controller.press_up(t + 220)
    controller.start_listening()
    feed_in_chunks(controller.feed_audio, beeps_in_a_room("YO"))
    controller.stop_listening()
    assert controller.text == "HIE YO "


def test_forgetting_timing_keeps_the_message_and_keeps_listening(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    while controller.letter_pause_s:
        controller.next_letter_pause()
    type_fluently(controller, "HI", 0.0)
    controller.start_listening()
    controller.forget_timing()
    assert controller.text == "HI "
    feed_in_chunks(controller.feed_audio, beeps_in_a_room("YO"))
    controller.stop_listening()
    assert controller.text == "HI YO "


def test_listening_to_a_room_with_no_beeps_types_nothing(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    controller.start_listening()
    noise = room_noise(20 * SAMPLE_RATE, SAMPLE_RATE, np.random.default_rng(1),
                       **SCENARIOS["talking louder than beeps"])
    feed_in_chunks(controller.feed_audio, noise)
    controller.stop_listening()
    assert controller.text == ""
