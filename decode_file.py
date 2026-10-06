"""Decode Morse beeps from a recording.

    python decode_file.py recording.wav

The file must be a 16-bit WAV. To make a test file, run make_test_audio.py.
"""

import sys

from morse_aac.adaptive import AdaptiveDecoder
from morse_aac.audio import events_from_audio, read_wav


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python decode_file.py recording.wav")
        sys.exit(1)
    samples, rate = read_wav(sys.argv[1])
    events, pitch = events_from_audio(samples, rate)
    if not events:
        print("No beeps found in that recording.")
        return
    decoder = AdaptiveDecoder()
    text = decoder.decode(events)
    presses = sum(e.is_on for e in events)
    print(f"Beep pitch: {pitch:.0f} Hz   Beeps found: {presses}   "
          f"Speed: about {decoder.estimated_wpm():.0f} words per minute")
    print()
    print(text)


if __name__ == "__main__":
    main()
