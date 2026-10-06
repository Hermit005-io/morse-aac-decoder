"""The microphone, wrapped so the rest of the code never touches the sound
library directly, and so its two ways of failing give a plain message.

    microphone = Microphone()
    microphone.start()              # raises MicrophoneError with a message to show
    for chunk in microphone.read(): # whatever has arrived since the last read
        ...
    microphone.stop()

Needs the `sounddevice` package, which is only imported when start() is called,
so everything else works without it.
"""

import queue

from .listener import SAMPLE_RATE

NO_SOUNDDEVICE_HELP = ("Listening needs the sounddevice package. Install it with:  "
                       "pip install sounddevice")
NO_MICROPHONE_HELP = ("Couldn't open a microphone. Check that one is connected and switched "
                      "on, and that your computer's privacy settings allow apps to use it.")


class MicrophoneError(Exception):
    """Carries a message written for the person using the app."""


class Microphone:
    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._chunks: queue.Queue = queue.Queue()
        self._stream = None

    def start(self) -> None:
        try:
            import sounddevice
        except (ImportError, OSError) as error:  # OSError: its sound library is missing
            raise MicrophoneError(NO_SOUNDDEVICE_HELP) from error

        def on_sound(indata, frames, time_info, status):
            # Runs on the sound library's own thread: just hand the sound over.
            self._chunks.put(indata[:, 0].copy())

        try:
            self._stream = sounddevice.InputStream(
                samplerate=self.sample_rate, channels=1, dtype="float32",
                blocksize=self.sample_rate // 20, callback=on_sound)
            self._stream.start()
        except sounddevice.PortAudioError as error:
            self._stream = None
            raise MicrophoneError(NO_MICROPHONE_HELP) from error

    def read(self, wait_s: float = 0.0) -> list:
        """The chunks of sound that have arrived. With wait_s, waits up to that
        long for the first one."""
        chunks = []
        try:
            if wait_s:
                chunks.append(self._chunks.get(timeout=wait_s))
            while True:
                chunks.append(self._chunks.get_nowait())
        except queue.Empty:
            pass
        return chunks

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
