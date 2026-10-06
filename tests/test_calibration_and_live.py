import pytest

from morse_aac.adaptive import AdaptiveDecoder
from morse_aac.calibration import CalibrationError, calibrate
from morse_aac.controller import Controller
from morse_aac.events import Event
from morse_aac.naive import NaiveDecoder
from morse_aac.profile import load_profile, save_profile
from morse_aac.session import LiveSession
from morse_aac.synth import SenderProfile, synthesize


# ---- calibration -------------------------------------------------------------------------
def test_calibration_recovers_textbook_timing():
    timing = calibrate(synthesize("PARIS", SenderProfile(unit_ms=150)))
    expected = {"dot": 150, "dash": 450, "intra": 150, "char": 450, "word": 1050}
    for kind, ms in expected.items():
        assert timing[kind] == pytest.approx(ms, rel=0.01)


def test_calibration_picks_up_personal_differences():
    sender = SenderProfile(unit_ms=150, dash_ratio=2.0, gap_scale=2.0)
    timing = calibrate(synthesize("PARIS", sender))
    assert timing["dash"] / timing["dot"] < 2.6      # clearly not the textbook 3
    assert timing["intra"] == pytest.approx(300, rel=0.1)


def test_calibration_rejects_wrong_number_of_presses():
    events = synthesize("PARI", SenderProfile())
    with pytest.raises(CalibrationError):
        calibrate(events)


def test_calibration_ignores_leading_silence():
    events = [Event(False, 2000)] + synthesize("PARIS", SenderProfile(unit_ms=100))
    assert calibrate(events)["dot"] == pytest.approx(100, rel=0.01)


# ---- profile -------------------------------------------------------------------------------
def test_profile_round_trip(tmp_path):
    path = tmp_path / "profile.json"
    assert load_profile(path) is None
    timing = {"dot": 1, "dash": 3, "intra": 1, "char": 3, "word": 7}
    save_profile(timing, {"press": 0.02, "gap": 0.03}, path)
    assert load_profile(path) == (timing, {"press": 0.02, "gap": 0.03})


def test_broken_profile_is_ignored(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text("{not json")
    assert load_profile(path) is None


def test_profile_with_impossible_numbers_is_ignored(tmp_path):
    path = tmp_path / "profile.json"
    timing = {"dot": 100, "dash": 300, "intra": 100, "char": 300, "word": 700}
    save_profile({**timing, "dot": 0}, None, path)       # a dot of no length
    assert load_profile(path) is None
    save_profile(timing, {"press": 0.02}, path)          # half the noise missing
    assert load_profile(path) == (timing, None)
    Controller(profile_path=path)                        # and the app still opens


# ---- live session -------------------------------------------------------------------------
def run_live(session: LiveSession, events: list[Event], start: float = 0.0,
             tail_ms: float = 3000.0) -> float:
    """Play events into a session in 'real time', ticking every 20ms."""
    t = start
    for event in events:
        if event.is_on:
            session.press_down(t)
            t += event.duration_ms
            session.press_up(t)
        else:
            end = t + event.duration_ms
            while t < end:
                t = min(end, t + 20)
                session.tick(t)
    end = t + tail_ms
    while t < end:
        t += 20
        session.tick(t)
    return t


def test_live_session_finishes_letters_and_words_by_the_clock():
    session = LiveSession(NaiveDecoder(100))
    end = run_live(session, synthesize("SOS", SenderProfile(unit_ms=100)), tail_ms=0)
    assert session.decoder.text == "SO"          # last S still in progress
    session.tick(end + 250)                      # past a character gap (200ms)...
    assert session.decoder.text == "SOS"         # ...so the S appears
    session.tick(end + 550)                      # past a word gap (500ms)...
    assert session.decoder.text == "SOS "        # ...so a space is added


def test_live_session_matches_batch_decoding():
    sender = SenderProfile(unit_ms=130, press_jitter=0.1, gap_jitter=0.1)
    for seed in range(5):
        events = synthesize("PLEASE CALL MY NURSE", sender, seed=seed)
        session = LiveSession(AdaptiveDecoder())
        run_live(session, events)
        session.decoder.finish()
        assert session.decoder.text == AdaptiveDecoder().decode(events)


def test_live_session_starts_decoding_after_a_pause_with_few_presses():
    session = LiveSession(AdaptiveDecoder())
    run_live(session, synthesize("HI", SenderProfile(unit_ms=150)))
    assert session.decoder.text == "HI "


def test_live_session_ignores_repeated_press_down():
    session = LiveSession(NaiveDecoder(100))
    session.press_down(0)
    session.press_down(50)      # e.g. keyboard auto-repeat
    session.press_up(100)
    session.tick(1000)
    assert session.decoder.text == "E "


# ---- controller (the app without the window) --------------------------------------------
class FakeSession:
    """Adapter so run_live can drive a Controller."""

    def __init__(self, controller):
        self.c = controller

    def press_down(self, t):
        self.c.press_down(t)

    def press_up(self, t):
        self.c.press_up(t)

    def tick(self, t):
        self.c.tick(t)


def fluent_controller(path) -> Controller:
    """A Controller set the way someone who knows the code by heart would set
    it: letters finished by their own learned rhythm, not after a fixed pause.
    (The tests below type at a steady textbook rhythm, a third of a second
    between letters. What a beginner needs is tested in test_learners.py.)"""
    controller = Controller(profile_path=path)
    while controller.letter_pause_s:
        controller.next_letter_pause()
    return controller


def test_controller_calibrates_saves_and_decodes(tmp_path):
    path = tmp_path / "profile.json"
    controller = fluent_controller(path)
    assert not controller.has_profile

    sender = SenderProfile(unit_ms=200, press_jitter=0.1, gap_jitter=0.1, dash_ratio=2.2)
    controller.start_calibration()
    t = run_live(FakeSession(controller), synthesize("PARIS", sender, seed=1), start=1000)
    assert controller.mode == "typing", controller.message
    assert path.exists()

    typed = synthesize("I AM COLD", sender, seed=2, spaces="code")
    run_live(FakeSession(controller), typed, start=t + 3000, tail_ms=1000)
    assert controller.text == "I AM COLD"

    # A new controller (next time the app opens) picks up the saved timing.
    again = Controller(profile_path=path)
    assert again.has_profile
    assert again.decoder.timing()["dot"] == pytest.approx(200, rel=0.15)


def test_controller_calibration_retry_message(tmp_path):
    controller = Controller(profile_path=tmp_path / "p.json")
    controller.start_calibration()
    run_live(FakeSession(controller), synthesize("PA", SenderProfile()), tail_ms=7000)
    assert controller.mode == "calibrating"
    assert "try again" in controller.message.lower()


def test_controller_delete_and_clear(tmp_path):
    controller = fluent_controller(tmp_path / "p.json")
    hello = synthesize("HELLO", SenderProfile(unit_ms=120))
    run_live(FakeSession(controller), hello, tail_ms=1000)
    assert controller.text == "HELLO"
    controller.delete_last()
    assert controller.text == "HELL"
    controller.clear()
    assert controller.text == ""


def test_speed_wording():
    from morse_aac.controller import describe_speed
    assert describe_speed(1.0) == "1 word per minute"
    assert describe_speed(1.46) == "1.5 words per minute"
    assert describe_speed(20.3) == "20 words per minute"


def type_two_words(controller, pause_ms):
    """Type HI, keep hands off for pause_ms, type YO. Returns the text."""
    perfect = SenderProfile(unit_ms=120)
    t = run_live(FakeSession(controller), synthesize("HI", perfect), tail_ms=pause_ms)
    run_live(FakeSession(controller), synthesize("YO", perfect), start=t, tail_ms=1000)
    return controller.text


def test_by_default_stopping_for_a_few_seconds_adds_a_space(tmp_path):
    controller = fluent_controller(tmp_path / "p.json")
    assert controller.spaces == "pause"
    assert type_two_words(controller, pause_ms=3000) == "HI YO"


def test_a_shorter_pause_adds_no_space(tmp_path):
    # 1.5 seconds is far beyond a normal word gap at this speed (0.84s), but
    # short of the 2.5 second pause, so the letters stay together.
    controller = fluent_controller(tmp_path / "p.json")
    assert type_two_words(controller, pause_ms=1500) == "HIYO"


def test_in_hold_only_mode_no_pause_adds_a_space(tmp_path):
    controller = fluent_controller(tmp_path / "p.json")
    controller.next_spaces_mode()
    assert controller.spaces == "code"
    assert type_two_words(controller, pause_ms=8000) == "HIYO"


def test_spaces_button_steps_through_all_three_modes_and_is_remembered(tmp_path):
    path = tmp_path / "p.json"
    controller = Controller(profile_path=path)
    seen = [controller.spaces]
    for _ in range(3):
        controller.next_spaces_mode()
        seen.append(controller.spaces)
    assert seen == ["pause", "code", "timing", "pause"]
    controller.next_spaces_mode()
    assert Controller(profile_path=path).spaces == "code"
    # Forgetting timing keeps settings.
    controller.forget_timing()
    assert Controller(profile_path=path).spaces == "code"


def test_holding_long_enough_shows_space_then_types_it(tmp_path):
    controller = fluent_controller(tmp_path / "p.json")
    perfect = SenderProfile(unit_ms=120)
    t = run_live(FakeSession(controller), synthesize("HI", perfect), tail_ms=1000)
    controller.press_down(t)
    assert controller.holding_symbol(t + 100) == "."
    assert controller.holding_symbol(t + 400) == "-"
    assert controller.holding_symbol(t + 2000) == "space"
    controller.press_up(t + 2000)
    run_live(FakeSession(controller), synthesize("YO", perfect), start=t + 2400, tail_ms=1000)
    assert controller.text == "HI YO"
