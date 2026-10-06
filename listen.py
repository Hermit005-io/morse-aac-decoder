"""Decode Morse beeps live from the microphone, in the terminal.

    python listen.py

(The app has the same thing behind its Listen button.)

Needs the `sounddevice` package (pip install sounddevice). On a Mac, the first
run will ask for microphone permission; allow it, then run this again.

It waits until it hears beeps, works out their pitch, then shows the decoded
text as it's heard. Press Ctrl+C to stop.

Works best with the sound source within about a metre of the microphone.
Background noise and talking are fine; a strong echo is not.

If you know the pitch, you can give it in Hz and skip the search:
    python listen.py 700

To listen for taps on the desk instead (one tap for a dot, two quick taps for
a dash; stop for a second to finish a letter):
    python listen.py taps
"""

import shutil
import sys

from morse_aac.adaptive import AdaptiveDecoder
from morse_aac.listener import SAMPLE_RATE, SoundListener
from morse_aac.microphone import Microphone, MicrophoneError
from morse_aac.taps import TapDecoder, TapListener


def main() -> None:
    microphone = Microphone()
    try:
        microphone.start()
    except MicrophoneError as error:
        print(error)
        print("To see what sound devices Python can find, run:\n    python -m sounddevice")
        sys.exit(1)
    try:
        if sys.argv[1:] == ["taps"]:
            listen_for_taps(microphone)
        else:
            listen(microphone)
    finally:
        microphone.stop()


def show(text: str) -> None:
    """Rewrite the current line with the end of `text`. Only as much as fits
    the window is shown: a line that wraps can't be rewritten in place."""
    width = shutil.get_terminal_size().columns - 1
    sys.stdout.write("\r" + text[-width:].ljust(width))
    sys.stdout.flush()


def listen(microphone: Microphone) -> None:
    pitch = float(sys.argv[1]) if len(sys.argv) > 1 else None
    # Whoever is sending the beeps, the decoder learns their timing from scratch.
    decoder = AdaptiveDecoder()
    listener = SoundListener(SAMPLE_RATE, decoder, pitch)

    print("Waiting for beeps... (Ctrl+C to stop)")
    announced = False
    shown = None
    try:
        while True:
            # A short wait, so Ctrl+C is noticed promptly on every system.
            for chunk in microphone.read(wait_s=0.2):
                listener.feed(chunk)
            if not listener.hearing_beeps:
                continue
            if not announced:
                print(f"Beep pitch: {listener.pitch:.0f} Hz. Decoding:\n")
                announced = True
            if decoder.text != shown:
                shown = decoder.text
                show(shown)
    except KeyboardInterrupt:
        decoder.finish()
        if announced:
            show("")
        print("\r" + decoder.text)


def listen_for_taps(microphone: Microphone) -> None:
    decoder = TapDecoder()
    listener = TapListener(SAMPLE_RATE, decoder)
    print("Listening for taps: one tap = dot, two quick taps = dash. (Ctrl+C to stop)\n")
    shown = None
    try:
        while True:
            for chunk in microphone.read(wait_s=0.2):
                listener.feed(chunk)
            now = f"{decoder.text}   {listener.pending_pattern}"
            if now != shown:
                shown = now
                show(shown)
    except KeyboardInterrupt:
        listener.finish()
        show("")
        print("\r" + decoder.text)


if __name__ == "__main__":
    main()
