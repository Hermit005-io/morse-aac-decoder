"""The International Morse code table, plus helpers to go between text and patterns.

A "pattern" is a string of '.' and '-' for one character, e.g. 'A' -> '.-'.
"""

CHAR_TO_PATTERN = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.", "!": "-.-.--",
    "/": "-..-.", "(": "-.--.", ")": "-.--.-", "&": ".-...", ":": "---...",
    ";": "-.-.-.", "=": "-...-", "+": ".-.-.", "-": "-....-", "_": "..--.-",
    '"': ".-..-.", "@": ".--.-.",
}

PATTERN_TO_CHAR = {pattern: char for char, pattern in CHAR_TO_PATTERN.items()}

# The standard Morse "error" signal is a run of 8 dots. Senders use it to say
# "delete what I just sent". People with unsteady timing rarely hit exactly 8,
# so any run of 7 or more dots counts.
ERROR_MIN_DOTS = 7


def is_error_signal(pattern: str) -> bool:
    return len(pattern) >= ERROR_MIN_DOTS and set(pattern) == {"."}


# Codes for space and delete, as used by Google's Gboard Morse keyboard (built
# with Tania Finlayson, a long-time Morse AAC user). They let someone type a
# space on purpose, instead of the decoder guessing one from a long pause, so
# pausing to think never adds a space by accident. None of these patterns is a
# letter in the standard table above.
SPACE_CODE = "..--"
DELETE_CODE = "----"


def encode(text: str) -> list[list[str]]:
    """Turn text into a list of words, each word a list of character patterns.

    Characters that have no Morse equivalent are skipped.
    """
    words = []
    for word in text.upper().split():
        patterns = [CHAR_TO_PATTERN[c] for c in word if c in CHAR_TO_PATTERN]
        if patterns:
            words.append(patterns)
    return words


def decode_pattern(pattern: str) -> str | None:
    """Look up one character pattern. Returns None if it isn't valid Morse."""
    return PATTERN_TO_CHAR.get(pattern)
