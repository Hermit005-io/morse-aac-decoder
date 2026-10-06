import statistics

import pytest

from morse_aac.adaptive import AdaptiveDecoder, standard_timing
from morse_aac.events import Event
from morse_aac.metrics import character_error_rate
from morse_aac.naive import NaiveDecoder
from morse_aac.synth import PHRASES, SenderProfile, synthesize

PERFECT = SenderProfile(unit_ms=120)


SEEDS = (0, 1, 2)


def average_cer(make_decoder, sender):
    return statistics.mean(
        character_error_rate(p, make_decoder().decode(synthesize(p, sender, seed=s)))
        for p in PHRASES for s in SEEDS
    )


@pytest.mark.parametrize("phrase", PHRASES)
def test_both_decoders_read_perfect_timing_exactly(phrase):
    events = synthesize(phrase, PERFECT)
    assert NaiveDecoder(120).decode(events) == phrase
    assert AdaptiveDecoder().decode(events) == phrase
    assert AdaptiveDecoder(standard_timing(120)).decode(events) == phrase


@pytest.mark.parametrize("wpm", [3, 5, 10, 20, 30])
def test_adaptive_needs_no_speed_setting(wpm):
    sender = SenderProfile(unit_ms=1200 / wpm)
    for phrase in PHRASES:
        assert AdaptiveDecoder().decode(synthesize(phrase, sender)) == phrase


def test_naive_breaks_when_speed_is_wrong():
    # Told 10 WPM, sender at 5 WPM: every dot looks like a dash.
    events = synthesize("I NEED WATER", SenderProfile(unit_ms=240))
    assert NaiveDecoder(120).decode(events) != "I NEED WATER"


def test_adaptive_follows_a_sender_who_slows_down():
    tired = SenderProfile(unit_ms=120, press_jitter=0.1, gap_jitter=0.1, drift=1.0)
    naive = average_cer(lambda: NaiveDecoder(120), tired)
    adaptive = average_cer(lambda: AdaptiveDecoder(standard_timing(120)), tired)
    assert adaptive < 0.05
    assert naive > 4 * adaptive


def test_adaptive_learns_short_dashes():
    short_dashes = SenderProfile(unit_ms=120, press_jitter=0.1, gap_jitter=0.1, dash_ratio=2.0)
    naive = average_cer(lambda: NaiveDecoder(120), short_dashes)
    adaptive = average_cer(lambda: AdaptiveDecoder(), short_dashes)
    assert adaptive < naive / 2


def test_adaptive_learns_long_gaps():
    slow_release = SenderProfile(unit_ms=120, press_jitter=0.1, gap_jitter=0.1, gap_scale=2.0)
    assert average_cer(lambda: NaiveDecoder(120), slow_release) > 0.5
    assert average_cer(lambda: AdaptiveDecoder(), slow_release) < 0.05


def test_timing_model_tracks_a_speed_change():
    decoder = AdaptiveDecoder(standard_timing(120))
    decoder.feed(synthesize(" ".join(PHRASES), SenderProfile(unit_ms=120, drift=1.0)))
    # By the end the sender is at 2x the unit (240ms dots).
    assert 190 < decoder.timing()["dot"] < 260


def test_eight_dots_delete_the_last_letter():
    u = 100
    events = synthesize("HI", SenderProfile(unit_ms=u))
    events.append(Event(False, 3 * u))
    for i in range(8):
        if i:
            events.append(Event(False, u))
        events.append(Event(True, u))
    assert AdaptiveDecoder(standard_timing(u)).decode(events) == "H"
    assert NaiveDecoder(u).decode(events) == "H"


def test_repair_fixes_one_borderline_press():
    # "..--" isn't a letter. Its third press was borderline (190ms with 100ms
    # dots / 300ms dashes), so repair reads it as a dot instead: "...-" = V.
    u = 100
    presses = [100, 100, 190, 300]
    events = []
    for i, p in enumerate(presses):
        if i:
            events.append(Event(False, u))
        events.append(Event(True, p))
    timing = standard_timing(u)
    assert AdaptiveDecoder(timing, repair=False).decode(events) == "?"
    decoder = AdaptiveDecoder(timing)
    assert decoder.decode(events) == "V"
    assert decoder.output[0].repaired


def test_confidence_is_lower_for_borderline_presses():
    decoder = AdaptiveDecoder(standard_timing(100))
    _, clear = decoder.classify_press(100)
    _, borderline = decoder.classify_press(175)
    assert clear > 0.9
    assert borderline < 0.5


def test_bootstrap_from_all_dots():
    # "HIS" is nothing but dots: no short/long split to find in the presses.
    for unit in (80, 150, 300):
        assert AdaptiveDecoder().decode(synthesize("HIS", SenderProfile(unit_ms=unit))) == "HIS"


# ---- word spaces typed with the space code ..-- ------------------------------------------
def test_space_code_types_spaces():
    for phrase in PHRASES:
        events = synthesize(phrase, PERFECT, spaces="code")
        assert AdaptiveDecoder(spaces="code").decode(events) == phrase
        assert NaiveDecoder(120, spaces="code").decode(events) == phrase


def test_in_code_mode_a_long_pause_is_not_a_space():
    u = 120
    events = synthesize("H", PERFECT) + [Event(False, 20 * u)] + synthesize("I", PERFECT)
    assert NaiveDecoder(u).decode(events) == "H I"               # timing mode: a space
    assert NaiveDecoder(u, spaces="code").decode(events) == "HI"  # code mode: just a pause


def test_delete_code_removes_the_last_letter():
    u = 120
    events = synthesize("HIX", PERFECT) + [Event(False, 3 * u)]
    for i in range(4):  # ---- : four dashes
        if i:
            events.append(Event(False, u))
        events.append(Event(True, 3 * u))
    assert NaiveDecoder(u, spaces="code").decode(events) == "HI"
    assert AdaptiveDecoder(standard_timing(u), spaces="code").decode(events) == "HI"


def _presses(pattern, u, gaps=None):
    """Events for one character; gaps[i] (in units) overrides the gap before press i."""
    events = []
    for i, symbol in enumerate(pattern):
        if i:
            events.append(Event(False, (gaps or {}).get(i, 1.0) * u))
        events.append(Event(True, u if symbol == "." else 3 * u))
    return events


def test_space_code_split_by_a_short_hesitation_is_still_a_space():
    # ..  (pause of 2.1 units, a hesitation)  --  would otherwise read as "IM":
    # 2.1 units is past the letter-break line, but short of a usual letter gap.
    u = 120
    events = (_presses("....", u) + [Event(False, 3 * u)] + _presses("..", u)
              + [Event(False, 3 * u)] + _presses("..--", u, gaps={2: 2.1})
              + [Event(False, 3 * u)] + _presses("-", u))
    assert NaiveDecoder(u, spaces="code").decode(events) == "HI T"
    assert AdaptiveDecoder(standard_timing(u), spaces="code").decode(events) == "HI T"


def test_real_i_then_m_with_a_normal_letter_gap_stays_letters():
    u = 120
    events = (_presses("....", u) + [Event(False, 3 * u)] + _presses("..", u)
              + [Event(False, 3 * u)] + _presses("--", u))
    assert AdaptiveDecoder(standard_timing(u), spaces="code").decode(events) == "HIM"


def test_only_the_i_m_split_is_rescued():
    # E + W (".", ".--") is also ..-- split in two, but it's common in real
    # words (NEW), so it's left alone even with a short gap.
    u = 120
    events = _presses(".", u) + [Event(False, 2.1 * u)] + _presses(".--", u)
    assert NaiveDecoder(u, spaces="code").decode(events) == "EW"


# ---- a space typed as one long hold ------------------------------------------------------
def test_long_hold_types_a_space():
    for phrase in PHRASES:
        events = synthesize(phrase, PERFECT, spaces="hold")
        assert AdaptiveDecoder(spaces="code").decode(events) == phrase
        assert NaiveDecoder(120, spaces="code").decode(events) == phrase


def test_long_hold_finishes_the_letter_in_progress_first():
    u = 120
    # H, I, then straight into a long hold with only a short gap, then T.
    events = (_presses("....", u) + [Event(False, 3 * u)] + _presses("..", u)
              + [Event(False, u), Event(True, 12 * u), Event(False, 3 * u)] + _presses("-", u))
    assert NaiveDecoder(u, spaces="code").decode(events) == "HI T"


def test_long_hold_is_just_a_dash_when_spaces_come_from_pausing():
    u = 120
    assert NaiveDecoder(u).decode([Event(True, 12 * u)]) == "T"


def test_long_hold_is_never_shorter_than_the_floor():
    # A fast sender: 60ms dots, 180ms dashes. 2.5 dashes is only 450ms, which
    # is too easy to do by accident, so the 600ms floor applies instead.
    decoder = NaiveDecoder(60, spaces="code")
    assert not decoder.is_long_hold(500)
    assert decoder.is_long_hold(700)


def test_long_hold_does_not_change_the_learned_dash_length():
    decoder = AdaptiveDecoder(standard_timing(120), spaces="code")
    before = decoder.timing()["dash"]
    decoder.press(3000)
    assert decoder.timing()["dash"] == before
    assert decoder.text == ""  # a space at the very start is dropped


# ---- a space from a fixed pause -----------------------------------------------------------
def test_fixed_pause_adds_a_space_and_shorter_pauses_do_not():
    u = 120
    def hi_then_yo(pause_ms):
        return synthesize("HI", PERFECT) + [Event(False, pause_ms)] + synthesize("YO", PERFECT)
    for make in (lambda: NaiveDecoder(u, spaces="pause"),
                 lambda: AdaptiveDecoder(standard_timing(u), spaces="pause")):
        assert make().decode(hi_then_yo(3000)) == "HI YO"
        assert make().decode(hi_then_yo(1500)) == "HIYO"


def test_fixed_pause_length_can_be_changed():
    u = 120
    events = synthesize("HI", PERFECT) + [Event(False, 1500)] + synthesize("YO", PERFECT)
    assert NaiveDecoder(u, spaces="pause", space_pause_ms=1000).decode(events) == "HI YO"


def test_for_a_very_slow_sender_the_pause_stretches_past_their_letter_gap():
    # 1 word per minute: letter gaps are 3.6 seconds, longer than the 2.5
    # second pause. Without stretching, every letter would be its own word.
    slow = SenderProfile(unit_ms=1200)
    decoder = NaiveDecoder(1200, spaces="pause")
    assert decoder.word_break_ms() == 6000
    assert decoder.decode(synthesize("HI", slow)) == "HI"


def test_hold_and_codes_still_work_alongside_the_pause():
    for phrase in PHRASES:
        for how in ("hold", "code"):
            events = synthesize(phrase, PERFECT, spaces=how)
            assert AdaptiveDecoder(spaces="pause").decode(events) == phrase
