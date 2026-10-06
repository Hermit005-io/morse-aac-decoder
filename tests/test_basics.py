from morse_aac.events import Event, events_from_timestamps
from morse_aac.metrics import character_error_rate, levenshtein, word_accuracy
from morse_aac.table import CHAR_TO_PATTERN, decode_pattern, encode, is_error_signal


def test_every_pattern_decodes_back_to_its_character():
    for char, pattern in CHAR_TO_PATTERN.items():
        assert decode_pattern(pattern) == char


def test_patterns_are_unique():
    assert len(set(CHAR_TO_PATTERN.values())) == len(CHAR_TO_PATTERN)


def test_encode_splits_words_and_skips_unknown_characters():
    assert encode("sos  hi~") == [["...", "---", "..."], ["....", ".."]]


def test_error_signal_is_seven_or_more_dots():
    assert is_error_signal("........")
    assert is_error_signal(".......")
    assert not is_error_signal("......")   # that's not even a letter, but not delete
    assert not is_error_signal("....-...")


def test_levenshtein():
    assert levenshtein("", "") == 0
    assert levenshtein("abc", "abc") == 0
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("abc", "") == 3
    assert levenshtein("", "ab") == 2


def test_character_error_rate_and_word_accuracy():
    assert character_error_rate("HELLO", "HELLO") == 0.0
    assert character_error_rate("HELLO", "HALLO") == 0.2
    assert word_accuracy("I NEED WATER", "I NEED WATTR") == 2 / 3


def test_events_from_timestamps():
    events = events_from_timestamps([(0, 100), (200, 500)])
    assert events == [Event(True, 100), Event(False, 100), Event(True, 300)]


def test_space_and_delete_codes_are_not_letters():
    from morse_aac.table import DELETE_CODE, SPACE_CODE
    assert decode_pattern(SPACE_CODE) is None
    assert decode_pattern(DELETE_CODE) is None
