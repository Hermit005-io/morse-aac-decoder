"""Morse by knocking on a desk: one tap for a dot, two quick taps for a dash.

The knocks here are made up (taps.knock_audio), as is the room. Passing means
the listener survives these, not that it has been proven on a real desk.
"""

import numpy as np
import pytest

from morse_aac.controller import Controller
from morse_aac.listener import SAMPLE_RATE
from morse_aac.room import SCENARIOS, room_noise
from morse_aac.taps import (
    KnockDetector,
    TapDecoder,
    TapListener,
    TapReader,
    knock_audio,
    tap_times,
)

QUIET = SCENARIOS["quiet room"]
TALKING = dict(SCENARIOS["talking louder than beeps"], clicks=False)


def knocks_in(audio, chunk=400):
    found = []
    detector = KnockDetector(SAMPLE_RATE, found.append)
    for start in range(0, len(audio), chunk):
        detector.feed(audio[start:start + chunk])
    return found


def heard(audio, chunk=400):
    decoder = TapDecoder()
    listener = TapListener(SAMPLE_RATE, decoder)
    for start in range(0, len(audio), chunk):
        listener.feed(audio[start:start + chunk])
    listener.finish()
    return decoder.text


def tapped(text, seed=0, room=None, **knock_settings):
    rng = np.random.default_rng(seed)
    audio = knock_audio(tap_times(text, rng), SAMPLE_RATE, rng, **knock_settings)
    if room is not None:
        audio = audio + room_noise(len(audio), SAMPLE_RATE, rng, **room)
    return audio


# ---- knocks to letters (no sound involved) -----------------------------------------------
def read(times_ms):
    decoder = TapDecoder()
    reader = TapReader(decoder)
    for t in times_ms:
        reader.knock(t)
    reader.finish()
    return decoder.text


def test_one_tap_is_a_dot_and_two_quick_taps_are_a_dash():
    assert read([0]) == "E"
    assert read([0, 150]) == "T"
    assert read([0, 600]) == "I"                 # two separate taps: two dots
    assert read([0, 600, 750]) == "A"            # tap, then a double tap
    assert read([0, 150, 700, 1300, 1900]) == "B"
    assert read([0, 140, 280]) == "T"            # a third quick tap is still one dash


def test_stopping_ends_the_letter_and_a_longer_stop_adds_a_space():
    assert read([0, 600, 2000]) == "IE"          # 1.4 s: a new letter
    assert read([0, 600, 3600]) == "I E"         # 3 s: a new word


def test_reader_shows_the_letter_so_far_and_finishes_by_the_clock():
    decoder = TapDecoder()
    reader = TapReader(decoder)
    reader.knock(0)
    reader.tick(100)
    assert reader.pending_pattern == "."         # might still become a dash
    reader.knock(150)
    reader.tick(200)
    assert reader.pending_pattern == "-"
    reader.tick(900)
    assert decoder.text == ""                    # not a full second yet
    reader.tick(1200)
    assert decoder.text == "T"
    reader.tick(2700)
    assert decoder.text == "T "


def test_the_space_and_delete_codes_work_by_tapping():
    def letter(pattern, start):
        times, t = [], start
        for symbol in pattern:
            times.append(t)
            if symbol == "-":
                times.append(t + 150)
            t += 600
        return times

    assert read(letter("....", 0) + letter("..", 4000) + letter("----", 8000)) == "H"


# ---- sound to knocks --------------------------------------------------------------------
def test_knocks_are_found_where_they_are():
    rng = np.random.default_rng(0)
    times = [400.0, 900.0, 1050.0, 2500.0]
    audio = knock_audio(times, SAMPLE_RATE, rng, tail_s=1.0)
    audio = audio + room_noise(len(audio), SAMPLE_RATE, rng, **QUIET)
    found = knocks_in(audio)
    assert len(found) == len(times)
    assert found == pytest.approx(times, abs=10)


def test_voices_and_rumble_are_not_knocks():
    knocks = 0
    for seed in range(3):
        noise = room_noise(20 * SAMPLE_RATE, SAMPLE_RATE, np.random.default_rng(seed), **TALKING)
        knocks += len(knocks_in(noise))
    assert knocks == 0   # in a minute of loud talking


def test_a_much_quieter_click_after_real_knocks_is_ignored():
    rng = np.random.default_rng(1)
    audio = knock_audio([300.0, 900.0, 1500.0], SAMPLE_RATE, rng, tail_s=2.0)
    faint = 0.03 * knock_audio([2500.0], SAMPLE_RATE, rng, tail_s=1.0)   # 30 dB quieter
    audio[: len(faint)] += faint[: len(audio)]
    audio = audio + room_noise(len(audio), SAMPLE_RATE, rng, level=0.0005, voices=False,
                               clicks=False)
    assert len(knocks_in(audio)) == 3


# ---- sound to text ------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["I NEED WATER", "PLEASE CALL MY NURSE", "THANK YOU"])
def test_tapped_messages_are_decoded(text):
    assert heard(tapped(text, room=QUIET)) == text


def test_soft_taps_and_loud_talking():
    assert heard(tapped("I AM COLD", seed=2, room=QUIET, loudness=0.1)) == "I AM COLD"
    assert heard(tapped("I AM COLD", seed=2, room=TALKING)) == "I AM COLD"


def test_result_does_not_depend_on_chunk_size():
    audio = tapped("HELP ME", seed=4, room=QUIET)
    assert [heard(audio, chunk) for chunk in (160, 400, 4000)] == ["HELP ME"] * 3


# ---- in the app ---------------------------------------------------------------------------
def test_app_hears_taps_into_the_text_when_set_to(tmp_path):
    path = tmp_path / "p.json"
    controller = Controller(profile_path=path)
    assert controller.hears_label() == "Hears: beeps"
    controller.next_hears()
    assert controller.hears_label() == "Hears: taps"
    assert Controller(profile_path=path).hears == "taps"     # remembered

    controller.start_listening()
    assert "none heard yet" in controller.status()
    assert "Two quick taps = dash" in controller.help_text()
    audio = tapped("YES", room=QUIET)
    for start in range(0, len(audio), 400):
        controller.feed_audio(audio[start:start + 400])
    assert "11 heard" in controller.status()     # Y -.--  E .  S ... : 7 + 1 + 3 taps
    controller.stop_listening()
    assert controller.text == "YES "


def test_letter_pause_button_reaches_a_tap_listener_already_running(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    controller.next_hears()
    controller.start_listening()
    controller.next_letter_pause()             # 1 s -> 1.5 s
    assert controller.mode == "listening"
    assert "stop for 1.5 seconds" in controller.help_text()
    # A space must leave time to start the next letter after one has shown.
    assert "Space:  stop for 3.75 seconds" in controller.help_text()
    assert "1.5 seconds" in controller.message


def test_clear_while_listening_also_drops_the_letter_being_tapped(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    controller.next_hears()
    controller.start_listening()
    audio = knock_audio([300.0, 900.0, 1500.0], SAMPLE_RATE, np.random.default_rng(0), tail_s=0.3)
    controller.feed_audio(audio)
    assert controller.pending_pattern() == "..."
    controller.clear()
    controller.feed_audio(np.zeros(3 * SAMPLE_RATE))
    assert controller.text == "" and controller.pending_pattern() == ""


def test_switching_what_it_hears_while_listening_starts_again(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    controller.start_listening()
    assert "beeps" in controller.status()
    controller.next_hears()
    assert controller.mode == "listening" and "taps" in controller.status()
