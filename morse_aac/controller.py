"""Everything the app does, without any window code.

The window (app.py) only draws things and passes along "switch down at time t"
/ "switch up at time t". Keeping the logic here means it can be tested without
opening a window.

Three modes:
  typing       presses are decoded into text
  calibrating  the person sends PARIS once, so the decoder can learn their
               timing (see calibration.py)
  listening    Morse heard by the microphone is decoded into the same text:
               beeps (listener.py) or taps on a desk (taps.py), whichever the
               Hears button is set to. The window owns the microphone and
               passes the sound in with feed_audio(); the switch is ignored
               meanwhile. Whoever is sending has their own rhythm, so they
               get their own decoder.

Letters: by default a letter is finished after 1 second without a press, and
by nothing shorter. The Letter pause button changes how long, or hands the decision
to the decoder's learned timing, which suits someone fluent and is far too
quick for someone still looking each letter up.

Word spaces: by default, stopping for 2.5 seconds adds one (one long hold, or
the space code ..--, also works). The Spaces button switches to "only on
purpose" (hold or code, so thinking pauses never add a space) or to the
radio-style learned pause. See decoder_base.py for both settings.
"""

from pathlib import Path

from .adaptive import AdaptiveDecoder
from .calibration import CALIBRATION_WORD, CalibrationError, calibrate, expected_labels
from .decoder_base import LETTER_PAUSES_PER_SPACE, DecodedChar
from .events import events_from_timestamps
from .listener import SAMPLE_RATE, SoundListener
from .profile import (
    DEFAULT_PATH,
    delete_profile,
    load_profile,
    load_settings,
    save_profile,
    save_settings,
)
from .session import LiveSession
from .table import CHAR_TO_PATTERN, DELETE_CODE, SPACE_CODE
from .taps import DEFAULT_LETTER_PAUSE_MS, TapDecoder, TapListener

# The ways of typing a space, in the order the Spaces button steps through
# them. The first is the default.
SPACE_MODE_ORDER = ("pause", "code", "timing")
# How long without a press finishes a letter, in seconds, in the order the
# Letter pause button steps through them. 0 means "learned": the decoder's own
# timing decides. A new user starts on DEFAULT_LETTER_PAUSE_S.
LETTER_PAUSE_ORDER = (0.7, 1.0, 1.5, 0.0)
DEFAULT_LETTER_PAUSE_S = 1.0
# What the Listen button listens for; the first is the default.
HEARS_ORDER = ("beeps", "taps")

# After the last calibration press, how long to wait before assuming they're done.
CALIBRATION_DONE_MS = 1500.0
# With presses still missing, how long a pause before assuming they stopped.
CALIBRATION_GIVE_UP_MS = 6000.0


def describe_seconds(seconds: float) -> str:
    """'1 second', '2.5 seconds', '3.75 seconds'."""
    shown = f"{round(seconds, 2):g}"
    return f"{shown} second{'' if shown == '1' else 's'}"


def describe_speed(wpm: float) -> str:
    """'12 words per minute', '1.5 words per minute', '1 word per minute'."""
    shown = f"{wpm:.0f}" if wpm >= 10 else f"{wpm:.1f}".removesuffix(".0")
    return f"{shown} word{'' if shown == '1' else 's'} per minute"


class Controller:
    def __init__(self, profile_path: Path = DEFAULT_PATH):
        self.profile_path = profile_path
        self.settings = load_settings(profile_path)
        self.spaces = self.settings.get("spaces")
        if self.spaces not in SPACE_MODE_ORDER:
            self.spaces = SPACE_MODE_ORDER[0]
        self.letter_pause_s = self.settings.get("letter_pause")
        if self.letter_pause_s not in LETTER_PAUSE_ORDER:
            self.letter_pause_s = DEFAULT_LETTER_PAUSE_S
        self.hears = self.settings.get("hears")
        if self.hears not in HEARS_ORDER:
            self.hears = HEARS_ORDER[0]
        saved = load_profile(profile_path)
        self.has_profile = saved is not None
        if saved:
            self._use_new_decoder(*saved)
        else:
            self._use_new_decoder()
        self.mode = "typing"
        self.message = ("Welcome back. Your saved timing is loaded." if saved else
                        "First time? Press Calibrate so the decoder can learn your timing. "
                        "(Or just start: it will learn as you go.)")
        self._cal_presses: list[tuple[float, float]] = []
        self._cal_down: float | None = None
        self._listener: SoundListener | TapListener | None = None

    # ---- input -----------------------------------------------------------------------
    def press_down(self, t_ms: float) -> None:
        if self.mode == "listening":
            return
        if self.mode == "calibrating":
            if self._cal_down is None:
                self._cal_down = t_ms
        else:
            self.session.press_down(t_ms)

    def press_up(self, t_ms: float) -> None:
        if self.mode == "listening":
            return
        if self.mode == "calibrating":
            if self._cal_down is not None:
                self._cal_presses.append((self._cal_down, t_ms))
                self._cal_down = None
        else:
            self.session.press_up(t_ms)

    def tick(self, t_ms: float) -> None:
        if self.mode == "listening":
            return  # sound keeps its own time, counted in samples
        if self.mode == "calibrating":
            needed = len(expected_labels()[0])
            if not self._cal_presses or self._cal_down is not None:
                return
            idle = t_ms - self._cal_presses[-1][1]
            # All presses in: finish after a short pause. Fewer: they may just
            # be slow, so only give up after a long pause.
            all_in = len(self._cal_presses) >= needed
            if idle > (CALIBRATION_DONE_MS if all_in else CALIBRATION_GIVE_UP_MS):
                self._finish_calibration()
        else:
            self.session.tick(t_ms)

    # ---- calibration -----------------------------------------------------------------
    def start_calibration(self) -> None:
        if self.mode == "listening":
            self.stop_listening()
        self.mode = "calibrating"
        self._cal_presses = []
        self._cal_down = None
        self.message = ("Send the word PARIS the way you really type, no faster, then stop. "
                        "Pause a little between letters.")

    def cancel_calibration(self) -> None:
        self.mode = "typing"
        self.message = "Calibration cancelled."

    def calibration_progress(self) -> tuple[int, int]:
        return len(self._cal_presses), len(expected_labels()[0])

    def _finish_calibration(self) -> None:
        events = events_from_timestamps(self._cal_presses)
        self._cal_presses = []
        try:
            timing = calibrate(events)
        except CalibrationError as error:
            self.message = f"{error} (Just send PARIS again.)"
            return
        self._use_new_decoder(timing)
        self.mode = "typing"
        self.save()
        self.has_profile = True
        self.message = (f"Calibrated: about {describe_speed(self.decoder.estimated_wpm())}. "
                        "Your timing is saved. Start typing!")

    # ---- listening --------------------------------------------------------------------
    def start_listening(self) -> None:
        self.decoder.flush()   # a half-typed letter belongs before what is heard
        heard = TapDecoder() if self.hears == "taps" else AdaptiveDecoder()
        # Both decoders write into the one text, so what is heard lands after
        # what was typed, and typing carries on after what was heard.
        heard.output = self.decoder.output
        self._separate()
        if self.hears == "taps":
            pause_ms = 1000 * self.letter_pause_s or DEFAULT_LETTER_PAUSE_MS
            self._listener = TapListener(SAMPLE_RATE, heard, pause_ms)
            self.message = ("Listening. Tap on the desk near the microphone: "
                            "one tap for a dot, two quick taps for a dash.")
        else:
            self._listener = SoundListener(SAMPLE_RATE, heard)
            self.message = ("Listening. Play Morse beeps near the microphone; "
                            "text appears once it hears them.")
        self.mode = "listening"

    def feed_audio(self, samples) -> None:
        """Sound from the microphone, at listener.SAMPLE_RATE, as it arrives."""
        if self._listener is not None:
            self._listener.feed(samples)

    def stop_listening(self, message: str = "Stopped listening.") -> None:
        if self._listener is not None:
            self._listener.flush()
            self._listener = None
            self._separate()
        self.mode = "typing"
        self.message = message

    def _separate(self) -> None:
        """A space between what was typed and what was heard, if one is needed."""
        if self.output and self.output[-1].char != " ":
            self.output.append(DecodedChar(" ", "", 1.0))

    def next_hears(self) -> None:
        """Switch what the Listen button listens for."""
        self.hears = HEARS_ORDER[(HEARS_ORDER.index(self.hears) + 1) % len(HEARS_ORDER)]
        self._save_setting("hears", self.hears)
        if self._listener is not None:   # already listening: start again, the new way
            self.stop_listening()
            self.start_listening()
        else:
            self.message = {
                "beeps": "Listen will now listen for Morse beeps, at any pitch.",
                "taps": "Listen will now listen for taps on the desk: "
                        "one tap for a dot, two quick taps for a dash.",
            }[self.hears]

    def hears_label(self) -> str:
        return f"Hears: {self.hears}"

    # ---- settings ---------------------------------------------------------------------
    def next_letter_pause(self) -> None:
        """Move to the next length of pause that finishes a letter."""
        order = LETTER_PAUSE_ORDER
        self.letter_pause_s = order[(order.index(self.letter_pause_s) + 1) % len(order)]
        self.decoder.letter_pause_ms = 1000 * self.letter_pause_s
        self._save_setting("letter_pause", self.letter_pause_s)
        if isinstance(self._listener, TapListener):   # taps keep to the same pause
            self.stop_listening()
            self.start_listening()
        if self.letter_pause_s:
            self.message = ("A letter is now finished when you stop for "
                            f"{describe_seconds(self.letter_pause_s)}.")
        else:
            self.message = ("Letters are now finished by your own rhythm, as the decoder has "
                            "learned it. Quick, but only comfortable once you know the code "
                            "by heart.")

    def letter_pause_label(self) -> str:
        if not self.letter_pause_s:
            return "Letter pause: learned"
        return f"Letter pause: {self.letter_pause_s:g} s"

    def next_spaces_mode(self) -> None:
        """Move to the next way of typing spaces."""
        order = SPACE_MODE_ORDER
        self.spaces = order[(order.index(self.spaces) + 1) % len(order)]
        self.decoder.spaces = self.spaces
        self._save_setting("spaces", self.spaces)
        self.message = {
            "pause": f"A space now appears when you stop for {self._pause_seconds()} seconds.",
            "code": "Spaces now come only from one long hold, or the space code "
                    f"{' '.join(SPACE_CODE)}. Pausing never adds one.",
            "timing": "Spaces now come from a pause that is long for you, as the decoder "
                      f"has learned your rhythm{self._at_least()}.",
        }[self.spaces]

    def spaces_label(self) -> str:
        return {"pause": f"Spaces: {self._pause_seconds()} s pause", "code": "Spaces: hold only",
                "timing": "Spaces: learned pause"}[self.spaces]

    def _pause_seconds(self) -> str:
        if self.spaces != "pause":
            return ""
        return f"{round(self.decoder.word_break_ms() / 1000, 2):g}"

    def _at_least(self) -> str:
        """With a letter pause set, even a learned space pause has a minimum."""
        if not self.letter_pause_s:
            return ""
        return f" (at least {describe_seconds(LETTER_PAUSES_PER_SPACE * self.letter_pause_s)})"

    @property
    def voice(self) -> str | None:
        """The chosen speaking voice, or None for the computer's default."""
        return self.settings.get("voice")

    def next_voice(self, voices: list[str]) -> str | None:
        """Move to the next voice in `voices` (then back to the default)."""
        options: list[str | None] = [None, *voices]
        current = options.index(self.voice) if self.voice in options else 0
        choice = options[(current + 1) % len(options)]
        self._save_setting("voice", choice)
        self.message = f"Voice: {choice or 'computer default'}"
        return choice

    def _save_setting(self, key: str, value) -> None:
        self.settings[key] = value
        save_settings(self.settings, self.profile_path)

    def help_text(self) -> str:
        if isinstance(self._listener, TapListener):
            reader = self._listener.reader
            letter = describe_seconds(reader.letter_pause_ms / 1000)
            space = describe_seconds(reader.space_pause_ms / 1000)
            return ("One tap = dot.   Two quick taps = dash.   Leave a clear beat between them.\n"
                    f"Letter:  stop for {letter}.   Space:  stop for {space}.\n"
                    "Talking is fine. Any other sharp sound (typing, a pen put down) "
                    "counts as a tap.")
        if self._listener is not None:
            return ("Hearing Morse beeps through the microphone, at any pitch and speed.\n"
                    "Keep the sound within about a metre. Talking is fine; a strong echo is not.")
        if self.letter_pause_s:
            letter = f"Letter:  stop for {describe_seconds(self.letter_pause_s)}"
        else:
            letter = "Letter:  a short pause, in your own rhythm"
        # Codes are spaced out (". . - -") because many fonts run hyphens together.
        delete = f"Delete last letter:  {' '.join(DELETE_CODE)}   (hold 4 times) or 8 taps"
        if self.spaces == "pause":
            return (f"{letter}\nSpace:  stop for {self._pause_seconds()} seconds"
                    f"   (or one long hold, or  {' '.join(SPACE_CODE)})\n{delete}")
        if self.spaces == "code":
            return (f"{letter}\nSpace:  one long hold, until the button says SPACE"
                    f"   (or  {' '.join(SPACE_CODE)})\n{delete}")
        return (f"{letter}\nSpace:  a pause that is long for you{self._at_least()}\n"
                "Delete last letter:  8 taps")

    def _use_new_decoder(self, timing: dict[str, float] | None = None,
                         noise: dict[str, float] | None = None) -> None:
        text_so_far = self.decoder.output if hasattr(self, "decoder") else []
        self.decoder = AdaptiveDecoder(timing, noise, spaces=self.spaces,
                                       letter_pause_ms=1000 * self.letter_pause_s)
        # A new timing model, the same message: calibrating or forgetting the
        # timing must not wipe what has been typed (or cut off a listener,
        # which writes into this same list).
        self.decoder.output = text_so_far
        self.session = LiveSession(self.decoder)

    # ---- other actions ------------------------------------------------------------------
    def clear(self) -> None:
        self.decoder.clear()
        if self._listener is not None:
            self._listener.clear()

    def delete_last(self) -> None:
        self.decoder.delete_last()

    def save(self) -> None:
        if self.decoder.initialized:
            save_profile(self.decoder.timing(), self.decoder.noise(), self.profile_path)

    def forget_timing(self) -> None:
        delete_profile(self.profile_path)
        self._use_new_decoder()
        self.has_profile = False
        self.message = "Saved timing deleted. The decoder will learn from scratch."

    # ---- what to show ---------------------------------------------------------------------
    @property
    def output(self) -> list[DecodedChar]:
        return self.decoder.output

    @property
    def text(self) -> str:
        return self.decoder.text

    def pending_pattern(self) -> str:
        if self.mode == "calibrating":
            return ""
        if self._listener is not None:
            return self._listener.pending_pattern
        return self.decoder.pending_pattern

    def holding_symbol(self, t_ms: float) -> str:
        if self.mode != "typing":
            return ""
        return self.session.state(t_ms).holding_symbol

    def status(self) -> str:
        if isinstance(self._listener, TapListener):
            heard = self._listener.knocks
            return f"Listening for taps: {heard} heard." if heard else \
                "Listening for taps: none heard yet."
        if self._listener is not None:
            if not self._listener.hearing_beeps:
                return "Listening: no beeps heard yet."
            sender = self._listener.session.decoder
            speed = (f", about {describe_speed(sender.estimated_wpm())}"
                     if sender.initialized else "")
            return f"Listening: beeps at {self._listener.pitch:.0f} Hz{speed}."
        if self.mode == "calibrating":
            done, needed = self.calibration_progress()
            guide = "   ".join(f"{c} {CHAR_TO_PATTERN[c]}" for c in CALIBRATION_WORD)
            return f"Calibrating: {guide}      presses: {done} of {needed}"
        if not self.decoder.initialized:
            return "Learning your timing: keep going, letters appear after a few presses."
        t = self.decoder.timing()
        return (f"Your speed: about {describe_speed(self.decoder.estimated_wpm())}   "
                f"(dot {t['dot']:.0f} ms, dash {t['dash']:.0f} ms, "
                f"letter gap {t['char']:.0f} ms)")
