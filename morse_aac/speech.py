"""Speaking text aloud with the computer's own built-in voices.

    Windows  the Speech API (voices like "Microsoft Zira Desktop"), via PowerShell
    macOS    the `say` command
    Linux    `espeak`, if installed

Text is always sent to the speaking program through its standard input, and a
voice name through an argument or environment variable, never pasted into a
command line. So nothing in a message (quotes, semicolons) can be run as part
of a command.
"""

import os
import re
import shutil
import subprocess
import sys

_WINDOWS_PREAMBLE = "Add-Type -AssemblyName System.Speech; " \
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
_WINDOWS_SPEAK = (_WINDOWS_PREAMBLE
                  + "if ($env:MORSE_AAC_VOICE) { $s.SelectVoice($env:MORSE_AAC_VOICE) }; "
                  + "$s.Speak([Console]::In.ReadToEnd())")
_WINDOWS_LIST = (_WINDOWS_PREAMBLE
                 + "$s.GetInstalledVoices() | Where-Object Enabled | "
                 + "ForEach-Object { $_.VoiceInfo.Name }")
_POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

# `say -v ?` lines look like:  "Samantha            en_US    # Hello! My name is..."
# Long names can leave just one space before the language code, so the line is
# matched from the language code and "#" rather than by counting spaces.
_MAC_VOICE_LINE = re.compile(r"^(.+?)\s+([a-z]{2,3}[_-][A-Za-z0-9]+)\s+#")


class Speaker:
    def __init__(self, platform: str = sys.platform, which=shutil.which):
        if platform == "win32" and which("powershell"):
            self.backend = "windows"
        elif which("say"):
            self.backend = "mac"
        elif which("espeak"):
            self.backend = "espeak"
        else:
            self.backend = None

    @property
    def available(self) -> bool:
        return self.backend is not None

    def command(self, voice: str | None = None) -> tuple[list[str], dict[str, str] | None]:
        """The program to run (text goes to its standard input), and any extra
        environment variables it needs."""
        if self.backend == "windows":
            return _POWERSHELL + [_WINDOWS_SPEAK], ({"MORSE_AAC_VOICE": voice} if voice else None)
        if self.backend == "mac":
            return ["say"] + (["-v", voice] if voice else []), None
        if self.backend == "espeak":
            return ["espeak", "--stdin"] + (["-v", voice] if voice else []), None
        raise RuntimeError("No speech program found on this computer.")

    def speak(self, text: str, voice: str | None = None) -> None:
        """Start speaking in the background, so the app doesn't freeze meanwhile."""
        argv, extra_env = self.command(voice)
        env = {**os.environ, **extra_env} if extra_env else None
        hide_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows only
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, env=env,
                                   creationflags=hide_window)
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()

    def voices(self) -> list[str]:
        """Names of the installed English voices (Windows, macOS). Takes about a
        second, so call it off the main thread. Empty if they can't be listed."""
        try:
            if self.backend == "windows":
                hide_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                out = subprocess.run(_POWERSHELL + [_WINDOWS_LIST], capture_output=True,
                                     text=True, timeout=20, creationflags=hide_window).stdout
                return [line.strip() for line in out.splitlines() if line.strip()]
            if self.backend == "mac":
                out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True,
                                     timeout=20).stdout
                return parse_mac_voices(out)
        except (OSError, subprocess.SubprocessError):
            pass
        return []


def parse_mac_voices(listing: str) -> list[str]:
    """English voice names from the output of `say -v ?`."""
    names = []
    for line in listing.splitlines():
        match = _MAC_VOICE_LINE.match(line)
        if match and match.group(2).lower().startswith("en"):
            names.append(match.group(1).strip())
    return names
