from morse_aac.controller import Controller
from morse_aac.speech import Speaker, parse_mac_voices


def only(*programs):
    """A stand-in for shutil.which that finds just the given programs."""
    return lambda name: f"/bin/{name}" if name in programs else None


def test_picks_the_right_speech_program_per_system():
    assert Speaker("win32", only("powershell")).backend == "windows"
    assert Speaker("darwin", only("say")).backend == "mac"
    assert Speaker("linux", only("espeak")).backend == "espeak"
    assert not Speaker("linux", only()).available


def test_voice_and_text_never_become_part_of_the_command():
    # The voice goes through an environment variable, the text through stdin.
    tricky = "Zira'; Remove-Item C:\\ -Recurse; '"
    argv, env = Speaker("win32", only("powershell")).command(tricky)
    assert not any(tricky in part for part in argv)
    assert env == {"MORSE_AAC_VOICE": tricky}

    argv, env = Speaker("darwin", only("say")).command("Samantha")
    assert argv == ["say", "-v", "Samantha"] and env is None


def test_default_voice_adds_nothing():
    assert Speaker("win32", only("powershell")).command(None)[1] is None
    assert Speaker("darwin", only("say")).command(None)[0] == ["say"]


def test_parse_mac_voice_list_keeps_english_voices():
    listing = (
        "Samantha            en_US    # Hello! My name is Samantha.\n"
        "Eddy (English (UK)) en_GB    # Hello! My name is Eddy.\n"
        "Thomas              fr_FR    # Bonjour, je m'appelle Thomas.\n"
    )
    assert parse_mac_voices(listing) == ["Samantha", "Eddy (English (UK))"]


def test_voice_choice_cycles_and_is_remembered(tmp_path):
    path = tmp_path / "p.json"
    controller = Controller(profile_path=path)
    voices = ["Microsoft David Desktop", "Microsoft Zira Desktop"]
    assert controller.voice is None
    assert controller.next_voice(voices) == "Microsoft David Desktop"
    assert controller.next_voice(voices) == "Microsoft Zira Desktop"
    controller.next_spaces_mode()  # saving one setting mustn't erase another
    again = Controller(profile_path=path)
    assert again.voice == "Microsoft Zira Desktop"
    assert again.spaces == "code"
    assert again.next_voice(voices) is None  # back to the computer's default
