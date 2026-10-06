"""The Morse AAC app: type with one switch, see and hear the text.

    python app.py

Press and hold the big button with the mouse/trackpad, or hold the SPACE bar.
Most accessibility switches plug in as a mouse click or a key press, so either
input works with one.

The Listen button decodes Morse heard by the microphone into the same text:
beeps, or taps on the desk, whichever the Hears button says (the computer will
ask for microphone access the first time).

No special permissions are needed: the app only listens to presses in its own
window (unlike the earlier pynput test, which listened to the whole keyboard
and so needed macOS Accessibility access).
"""

import threading
import time
import tkinter as tk

from morse_aac.controller import Controller
from morse_aac.microphone import Microphone, MicrophoneError
from morse_aac.speech import Speaker

# Colours (light theme).
BACKGROUND = "#f9f9f7"
PANEL = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
SWITCH_IDLE = "#dbe6f5"
SWITCH_PRESSED = "#2a78d6"
SURE = INK
FAIRLY_SURE = "#8a5a00"
GUESS = "#c42f2f"

FONT = ("Helvetica", 14)
FONT_SMALL = ("Helvetica", 12)
FONT_TEXT = ("Helvetica", 30)
FONT_MORSE = ("Courier", 30, "bold")

# Holding SPACE makes the computer repeat the key. A release followed by a
# press within this many ms is a repeat, not a real release.
AUTOREPEAT_MS = 60
TICK_MS = 20


SWITCH_TEXT = "PRESS AND HOLD HERE\n(or hold the SPACE bar)"
SWITCH_SPACE_TEXT = "SPACE\n(let go now)"
SWITCH_LISTENING_TEXT = "LISTENING FOR {}\n(click Stop listening to type again)"


def now_ms() -> float:
    return time.perf_counter() * 1000


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.controller = Controller()
        self.speaker = Speaker()
        self.voices: list[str] | None = None  # filled in by a background thread
        if self.speaker.available:
            threading.Thread(target=self._load_voices, daemon=True).start()
        self.microphone: Microphone | None = None  # set while listening
        self._space_down = False
        self._pending_release = None
        self._switch_down = False
        self._shown_text = None

        root.title("Morse AAC Decoder")
        root.configure(bg=BACKGROUND)
        root.geometry("980x680")
        root.minsize(720, 560)

        self.status = tk.Label(root, font=FONT_SMALL, fg=INK_SECONDARY, bg=BACKGROUND,
                               anchor="w", justify="left")
        self.status.pack(fill="x", padx=20, pady=(16, 4))

        self.message = tk.Label(root, font=FONT, fg=INK, bg=BACKGROUND, anchor="w",
                                justify="left", wraplength=920)
        self.message.pack(fill="x", padx=20, pady=(0, 10))

        # Bottom-up, so the buttons and the switch always stay on screen and
        # the text box gets whatever room is left.
        settings = tk.Frame(root, bg=BACKGROUND)
        settings.pack(side="bottom", fill="x", padx=20, pady=(0, 16))
        self.calibrate_button = self._button(settings, "Calibrate", self._calibrate)
        self.letter_pause_button = self._button(settings, "", self._next_letter_pause)
        self.spaces_button = self._button(settings, "", self._next_spaces_mode)
        self.voice_button = self._button(settings, "Voice", self._next_voice, enabled=False)
        self._button(settings, "Forget my timing", self._forget)

        actions = tk.Frame(root, bg=BACKGROUND)
        actions.pack(side="bottom", fill="x", padx=20, pady=(0, 8))
        self._button(actions, "Speak", self._speak, enabled=self.speaker.available)
        self.listen_button = self._button(actions, "Listen", self._listen)
        self.hears_button = self._button(actions, "", self._next_hears)
        self._button(actions, "Delete last letter", self._delete_last)
        self._button(actions, "Clear", self._clear)

        self.switch = tk.Label(root, text=SWITCH_TEXT,
                               font=("Helvetica", 22, "bold"), fg=INK, bg=SWITCH_IDLE,
                               height=4, cursor="hand2")
        self.switch.pack(side="bottom", fill="x", padx=20, pady=12)
        self.switch.bind("<ButtonPress-1>", lambda e: self._down())
        self.switch.bind("<ButtonRelease-1>", lambda e: self._up())

        current_row = tk.Frame(root, bg=BACKGROUND)
        current_row.pack(side="bottom", fill="x", padx=20)
        tk.Label(current_row, text="Letter so far:", font=FONT, fg=INK_SECONDARY,
                 bg=BACKGROUND).pack(side="left")
        self.current = tk.Label(current_row, font=FONT_MORSE, fg=INK, bg=BACKGROUND,
                                anchor="w", text=" ")
        self.current.pack(side="left", padx=12)

        self.help = tk.Label(root, font=FONT, fg=INK, bg=BACKGROUND, anchor="w",
                             justify="left")
        self.help.pack(side="bottom", fill="x", padx=20, pady=(0, 8))
        tk.Label(root, font=FONT_SMALL, fg=INK_SECONDARY, bg=BACKGROUND, anchor="w",
                 justify="left",
                 text="Black = sure.   Brown, underlined = fairly sure.   "
                      "Red, underlined = a guess."
                 ).pack(side="bottom", fill="x", padx=20, pady=(4, 2))

        self.text = tk.Text(root, height=3, font=FONT_TEXT, wrap="word", bg=PANEL, fg=INK,
                            relief="flat", padx=16, pady=12, highlightthickness=1,
                            highlightbackground="#e1e0d9", takefocus=0, cursor="arrow")
        self.text.pack(fill="both", expand=True, padx=20)
        self.text.tag_configure("sure", foreground=SURE)
        self.text.tag_configure("fairly", foreground=FAIRLY_SURE, underline=True)
        self.text.tag_configure("guess", foreground=GUESS, underline=True)
        self.text.configure(state="disabled")

        root.bind("<KeyPress-space>", self._space_press)
        root.bind("<KeyRelease-space>", self._space_release)
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.focus_force()
        self._tick()

    def _button(self, parent, label, command, enabled=True):
        # takefocus=0: otherwise SPACE would also "click" whichever button has focus.
        button = tk.Button(parent, text=label, command=command, font=FONT, takefocus=0,
                           state="normal" if enabled else "disabled", padx=10, pady=4)
        button.pack(side="left", padx=(0, 10))
        return button

    # ---- the switch ---------------------------------------------------------------------
    def _down(self) -> None:
        if not self._switch_down and self.microphone is None:
            self._switch_down = True
            self.controller.press_down(now_ms())
            self.switch.configure(bg=SWITCH_PRESSED, fg="white")

    def _up(self, t_ms: float | None = None) -> None:
        if self._switch_down:
            self._switch_down = False
            self.controller.press_up(t_ms if t_ms is not None else now_ms())
            self.switch.configure(bg=SWITCH_IDLE, fg=INK)

    def _space_press(self, _event) -> None:
        if self._pending_release is not None:
            # A release came in just before this press: that's key repeat.
            self.root.after_cancel(self._pending_release)
            self._pending_release = None
            return
        if not self._space_down:
            self._space_down = True
            self._down()

    def _space_release(self, _event) -> None:
        release_time = now_ms()
        if self._pending_release is not None:
            self.root.after_cancel(self._pending_release)
        self._pending_release = self.root.after(
            AUTOREPEAT_MS, lambda: self._confirm_space_release(release_time))

    def _confirm_space_release(self, release_time: float) -> None:
        self._pending_release = None
        self._space_down = False
        self._up(release_time)  # use when it really happened, not now

    # ---- buttons -----------------------------------------------------------------------
    def _listen(self) -> None:
        if self.microphone is not None:
            self._stop_listening()
            return
        microphone = Microphone()
        try:
            microphone.start()
        except MicrophoneError as error:
            self.controller.message = str(error)
            return
        self.microphone = microphone
        self.controller.start_listening()

    def _stop_listening(self) -> None:
        if self.microphone is not None:
            self.microphone.stop()
            self.microphone = None
            self.controller.stop_listening()

    def _calibrate(self) -> None:
        self._stop_listening()
        if self.controller.mode == "calibrating":
            self.controller.cancel_calibration()
        else:
            self.controller.start_calibration()

    def _speak(self) -> None:
        if self.speaker.available and self.controller.text.strip():
            self.speaker.speak(self.controller.text.lower(), self.controller.voice)

    def _load_voices(self) -> None:
        # Listing voices takes about a second, so it happens off the main
        # thread; the window picks up the result on its next redraw.
        self.voices = self.speaker.voices()

    def _next_voice(self) -> None:
        if self.voices:
            voice = self.controller.next_voice(self.voices)
            self.speaker.speak("Hello, this is my voice.", voice)

    def _next_spaces_mode(self) -> None:
        self.controller.next_spaces_mode()

    def _next_letter_pause(self) -> None:
        self.controller.next_letter_pause()

    def _next_hears(self) -> None:
        self.controller.next_hears()

    def _delete_last(self) -> None:
        self.controller.delete_last()

    def _clear(self) -> None:
        self.controller.clear()

    def _forget(self) -> None:
        self.controller.forget_timing()

    def _close(self) -> None:
        self._stop_listening()
        self.controller.save()
        self.root.destroy()

    # ---- redrawing -----------------------------------------------------------------------
    def _tick(self) -> None:
        t = now_ms()
        if self.microphone is not None:
            for chunk in self.microphone.read():
                self.controller.feed_audio(chunk)
        self.controller.tick(t)
        self._redraw(t)
        self.root.after(TICK_MS, self._tick)

    def _redraw(self, t: float) -> None:
        c = self.controller
        self.status.configure(text=c.status())
        self.message.configure(text=c.message)
        self.calibrate_button.configure(
            text="Cancel calibration" if c.mode == "calibrating" else "Calibrate")
        self.letter_pause_button.configure(text=c.letter_pause_label())
        self.spaces_button.configure(text=c.spaces_label())
        self.hears_button.configure(text=c.hears_label())
        listening = self.microphone is not None
        self.listen_button.configure(text="Stop listening" if listening else "Listen")
        self.help.configure(text=c.help_text())
        if self.voices:
            name = (c.voice or "default").replace("Microsoft ", "").replace(" Desktop", "")
            self.voice_button.configure(state="normal", text=f"Voice: {name}")

        holding = c.holding_symbol(t)
        long_hold = holding == "space"
        current = c.pending_pattern() + ("   [space]" if long_hold else holding)
        self.current.configure(text=current or " ")
        # Tell the person the moment a hold is long enough to count as a space.
        self.switch.configure(text=SWITCH_LISTENING_TEXT.format(c.hears.upper()) if listening
                              else SWITCH_SPACE_TEXT if long_hold else SWITCH_TEXT)

        shown = [(ch.char, ch.confidence) for ch in c.output]
        if shown != self._shown_text:
            self._shown_text = shown
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            for char, confidence in shown:
                tag = "sure" if confidence >= 0.6 else "fairly" if confidence >= 0.3 else "guess"
                self.text.insert("end", char, "sure" if char == " " else tag)
            self.text.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
