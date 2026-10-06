"""Typing by someone who is still learning the code: the letter pause, and the
second opinion that rescues a decoder started from the wrong timing.

Both exist because of how the app's first real user typed. See
learner_report.py for the measurements; these tests pin the behaviour down.
"""

import pytest

from morse_aac.adaptive import AdaptiveDecoder, _clear_split, standard_timing
from morse_aac.controller import LETTER_PAUSE_ORDER, Controller
from morse_aac.decoder_base import LONG_HOLD_MIN_MS
from morse_aac.metrics import character_error_rate
from morse_aac.naive import NaiveDecoder
from morse_aac.session import LiveSession
from morse_aac.synth import PHRASES, Learner, SenderProfile, synthesize, type_live

QUICK_CALIBRATION = standard_timing(60)   # PARIS tapped out at 20 words per minute


def typed_by_a_learner(decoder, text, seed=0, learner=None):
    type_live(LiveSession(decoder), text, learner or Learner(), seed=seed)
    decoder.finish()
    return decoder.text


# ---- the letter pause -----------------------------------------------------------------
def test_a_gap_shorter_than_the_letter_pause_never_ends_a_letter():
    # At 100 ms a unit the textbook decoder ends a letter after 200 ms.
    slow_fingers = [(True, 100), (False, 400), (True, 300)]   # . then - : the letter A
    without = NaiveDecoder(100)
    with_pause = NaiveDecoder(100, letter_pause_ms=1000)
    for decoder in (without, with_pause):
        for is_on, ms in slow_fingers:
            (decoder.press if is_on else decoder.gap)(ms)
        decoder.finish()
    assert without.text == "ET"
    assert with_pause.text == "A"


def test_a_gap_longer_than_the_letter_pause_always_ends_a_letter():
    decoder = NaiveDecoder(100, letter_pause_ms=1000)
    for is_on, ms in [(True, 100), (False, 1200), (True, 300), (False, 2600), (True, 100)]:
        (decoder.press if is_on else decoder.gap)(ms)
    decoder.finish()
    # 1.2 s ends the letter. A space takes 2.5 letter pauses, as it takes 2.5
    # times as long as ending a letter in textbook timing.
    assert decoder.text == "ET E"


def test_the_letter_pause_overrules_the_model_both_ways():
    # A model that believes this person's gaps inside letters run to 2 seconds
    # would keep a letter open that long. With a 1 second pause set, it can't.
    patient = {"dot": 300, "dash": 900, "intra": 1500, "char": 4500, "word": 10500}
    events = [(True, 300), (False, 1300), (True, 900)]
    without = AdaptiveDecoder(patient, spaces="pause")
    with_pause = AdaptiveDecoder(patient, spaces="pause", letter_pause_ms=1000)
    for decoder in (without, with_pause):
        for is_on, ms in events:
            (decoder.press if is_on else decoder.gap)(ms)
        decoder.finish()
    assert without.text == "A"
    assert with_pause.text == "ET"


def test_live_session_waits_for_the_letter_pause():
    session = LiveSession(AdaptiveDecoder(QUICK_CALIBRATION, letter_pause_ms=1000))
    session.press_down(0)
    session.press_up(60)             # one quick tap
    session.tick(60 + 900)
    assert session.decoder.pending_pattern == "."    # still room to carry on
    session.tick(60 + 1020)
    assert session.decoder.text == "E"


def test_the_first_letter_of_all_keeps_to_the_letter_pause_too():
    # No calibration and no saved timing: the decoder has no model until the
    # first pause, but the person has still been told "stop for 1 second".
    session = LiveSession(AdaptiveDecoder(spaces="pause", letter_pause_ms=1000))
    for start in (0, 500):                     # two taps: the letter I
        session.press_down(start)
        session.press_up(start + 130)
    session.tick(630 + 900)
    assert session.decoder.text == ""
    session.tick(630 + 1020)
    assert session.decoder.text == "I"


def test_a_space_always_leaves_time_to_start_the_next_letter():
    def space_after_ms(spaces, letter_pause_ms):
        return AdaptiveDecoder(QUICK_CALIBRATION, spaces=spaces,
                               letter_pause_ms=letter_pause_ms).word_break_ms()

    assert space_after_ms("pause", 700) == 2500
    assert space_after_ms("pause", 1000) == 2500
    assert space_after_ms("pause", 1500) == 3750       # not 2500: a second isn't enough
    assert space_after_ms("timing", 1500) == 3750      # the learned gap alone is far shorter


def test_repair_never_ends_a_letter_inside_the_letter_pause():
    # . - . - is not a letter. The doubtful part is the long gap in the middle,
    # but with a letter pause that gap cannot have been a letter break, so the
    # repair has to be a misread press.
    def decode(**settings):
        decoder = NaiveDecoder(100, repair=True, **settings)
        for is_on, ms in [(True, 100), (False, 100), (True, 300), (False, 190),
                          (True, 100), (False, 100), (True, 230)]:
            (decoder.press if is_on else decoder.gap)(ms)
        decoder.finish()
        return decoder.text

    assert decode() == "AA"                            # split at the doubtful gap
    assert decode(letter_pause_ms=1000) == "L"         # flipped the doubtful press


def test_overruled_gaps_teach_the_model_about_gaps_and_nothing_else():
    decoder = AdaptiveDecoder(QUICK_CALIBRATION, letter_pause_ms=1000)
    before = decoder.timing()
    assert decoder.classify_gap(400)[0] != "intra"   # the model alone would end the letter
    for _ in range(10):
        decoder.gap(400)
    after = decoder.timing()
    assert after["intra"] > 4 * before["intra"]      # most of the way from 60 ms to 400
    assert after["dot"] == before["dot"] and after["dash"] == before["dash"]
    for _ in range(20):
        decoder.gap(400)
    assert decoder.timing()["intra"] == pytest.approx(400, rel=0.1)


def test_the_letter_pause_is_what_lets_a_learner_type():
    # The reported bug: after one press the letter was finished before the
    # second could be made, and the message came out as E's and T's.
    hurried = typed_by_a_learner(AdaptiveDecoder(spaces="pause"), "I NEED WATER")
    assert character_error_rate("I NEED WATER", hurried) > 0.5
    assert set(hurried) <= set("ETIANM ")

    errors = [character_error_rate(p, typed_by_a_learner(
        AdaptiveDecoder(spaces="pause", letter_pause_ms=1000), p, seed=i))
        for i, p in enumerate(PHRASES[:8])]
    assert sum(errors) / len(errors) < 0.10


# ---- the second opinion -------------------------------------------------------------------
def test_clear_split_only_speaks_when_there_are_plainly_two_groups():
    dots, dashes, holds = [120, 130, 140, 125], [430, 450, 480], [1800, 1900, 2000]
    short, long, boundary = _clear_split(dots + dashes)
    assert short == pytest.approx(129, abs=2) and long == pytest.approx(453, abs=2)
    assert 140 < boundary < 430
    assert _clear_split(dots) is None                       # one group
    assert _clear_split(dots + dashes[:2]) is None          # too few dashes to be sure
    assert _clear_split(dots + dashes + holds) is None      # three groups
    assert _clear_split([100, 130, 160, 200, 250, 320]) is None   # a spread, no hole


def test_a_decoder_started_far_too_fast_is_restarted_from_the_presses():
    # Calibrated at 60 ms a dot; really 250 ms dots and 800 ms dashes. Read
    # with that model, the dots are dashes and the dashes are long holds.
    slow = Learner(dot_ms=250, dash_ms=800, inside_ms=400, gap_wobble=0.2)
    text = "PLEASE CALL MY NURSE"

    stuck = AdaptiveDecoder(QUICK_CALIBRATION, spaces="pause", letter_pause_ms=1000,
                            second_opinion=False)
    assert character_error_rate(text, typed_by_a_learner(stuck, text, learner=slow)) > 0.5

    decoder = AdaptiveDecoder(QUICK_CALIBRATION, spaces="pause", letter_pause_ms=1000)
    out = typed_by_a_learner(decoder, text, learner=slow)
    assert decoder.restarts >= 1
    assert decoder.timing()["dot"] == pytest.approx(250, rel=0.3)
    assert out.endswith("CALL MY NURSE")   # wrong until the restart, right after it


def test_dots_and_long_holds_are_not_mistaken_for_dots_and_dashes():
    # Someone practising: E, space, E, space... Two clear groups of presses,
    # but the model is right about both, so it must not be restarted.
    own = {"dot": 130, "dash": 450, "intra": 350, "char": 1200, "word": 2800}
    decoder = AdaptiveDecoder(own, spaces="pause", letter_pause_ms=1000)
    hold = 3 * LONG_HOLD_MIN_MS
    for i in range(8):
        decoder.press(130 + 5 * i)
        decoder.gap(1500)
        decoder.press(hold + 20 * i)
        decoder.gap(1500)
    decoder.finish()
    assert decoder.restarts == 0
    assert decoder.text == " ".join("E" * 8)


def test_the_second_opinion_leaves_a_working_decoder_alone():
    def decode_all(sender, timing, **settings):
        restarts, errors = 0, []
        for seed, phrase in enumerate(PHRASES):
            for sent, spaces in (("timing", "timing"), ("hold", "code")):
                decoder = AdaptiveDecoder(timing, spaces=spaces, **settings)
                out = decoder.decode(synthesize(phrase, sender, seed=seed, spaces=sent))
                restarts += decoder.restarts
                errors.append(character_error_rate(phrase, out))
        return restarts, sum(errors) / len(errors)

    # A steady sender, a decoder calibrated to them: it never has cause to speak.
    steady = SenderProfile(unit_ms=120, press_jitter=0.1, gap_jitter=0.1)
    assert decode_all(steady, standard_timing(120))[0] == 0

    # An unsteady one, and no calibration: now and then the presses do fall
    # into two groups by chance and it restarts. That must not cost accuracy.
    shaky = SenderProfile(unit_ms=120, press_jitter=0.3, gap_jitter=0.3, dash_ratio=2.5)
    _, with_it = decode_all(shaky, None)
    _, without = decode_all(shaky, None, second_opinion=False)
    assert with_it < without + 0.01


# ---- in the app ---------------------------------------------------------------------------
class ControllerSession:
    """Lets type_live drive a Controller the way it drives a LiveSession."""

    def __init__(self, controller):
        self.c = controller
        self.press_down, self.press_up, self.tick = (
            controller.press_down, controller.press_up, controller.tick)

    @property
    def decoder(self):
        return self.c.decoder


def test_the_app_gives_a_learner_a_second_to_carry_on_by_default(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    assert controller.letter_pause_s == 1.0
    assert "stop for 1 second" in controller.help_text()
    type_live(ControllerSession(controller), "HELP ME", Learner(press_wobble=0.1), seed=3)
    assert controller.text == "HELP ME"


def test_letter_pause_button_steps_through_the_choices_and_is_remembered(tmp_path):
    path = tmp_path / "p.json"
    controller = Controller(profile_path=path)
    seen = []
    for _ in LETTER_PAUSE_ORDER:
        controller.next_letter_pause()
        seen.append(controller.letter_pause_label())
    assert seen == ["Letter pause: 1.5 s", "Letter pause: learned", "Letter pause: 0.7 s",
                    "Letter pause: 1 s"]
    controller.next_letter_pause()
    assert controller.decoder.letter_pause_ms == 1500
    assert controller.spaces_label() == "Spaces: 3.75 s pause"   # see the test above
    again = Controller(profile_path=path)
    assert again.letter_pause_s == 1.5 and again.decoder.letter_pause_ms == 1500
    again.forget_timing()                         # a new decoder keeps the setting
    assert again.decoder.letter_pause_ms == 1500


def test_calibrating_or_forgetting_timing_keeps_what_was_typed(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    type_live(ControllerSession(controller), "HELP", Learner(press_wobble=0.1), seed=1)
    assert controller.text == "HELP"
    controller.forget_timing()
    assert controller.text == "HELP"
