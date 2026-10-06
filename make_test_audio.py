"""Make a WAV file of Morse beeps, sent by an imperfect synthetic person.

    python make_test_audio.py "I NEED WATER"

Creates test_audio.wav. Useful two ways:
  * python decode_file.py test_audio.wav    (decode it straight from the file)
  * play it out loud from your phone while running listen.py   (the microphone test)

Optional settings, e.g.:
    python make_test_audio.py "HELLO" --wpm 8 --jitter 0.2 --slowdown 0.5 --noise 0.3
"""

import argparse

from morse_aac.audio import synthesize_audio, write_wav
from morse_aac.synth import SenderProfile, synthesize


def main() -> None:
    parser = argparse.ArgumentParser(description="Make a WAV file of Morse beeps.")
    parser.add_argument("text", help="what to send, in quotes")
    parser.add_argument("--wpm", type=float, default=10, help="speed (default 10)")
    parser.add_argument("--jitter", type=float, default=0.15,
                        help="timing wobble, 0.15 = about 15%% (default 0.15)")
    parser.add_argument("--slowdown", type=float, default=0.0,
                        help="gradual slowdown, 0.5 = 1.5x slower by the end (default 0)")
    parser.add_argument("--noise", type=float, default=0.0,
                        help="background hiss, 0.3 = 30%% of beep loudness (default 0)")
    parser.add_argument("--pitch", type=float, default=700, help="beep pitch in Hz (default 700)")
    parser.add_argument("--out", default="test_audio.wav", help="output file name")
    args = parser.parse_args()

    sender = SenderProfile(unit_ms=1200 / args.wpm, press_jitter=args.jitter,
                           gap_jitter=args.jitter, drift=args.slowdown)
    events = synthesize(args.text, sender)
    rate = 8000
    audio = synthesize_audio(events, rate, args.pitch, noise_level=args.noise,
                             lead_in_ms=1000)
    write_wav(args.out, audio, rate)
    print(f"Wrote {args.out} ({len(audio) / rate:.1f} seconds)")


if __name__ == "__main__":
    main()
