"""Every test runs isolated: own cache/config dirs, no PO-token server, no lrclib.net."""

import shutil
import subprocess
from pathlib import Path

import pytest

import noaap.pot
import noaap.service


class NoLyrics:
    """What a Service gets instead of a real lrclib client: nothing is found, nothing is asked."""

    def get(self, artist, title, album=None, length=None, skip=()):
        return None

    def by_id(self, lrclib_id):
        return None


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    started = []
    monkeypatch.setattr(noaap.pot, "ensure_server", lambda *a, **k: started.append(a) or False)
    # a Service builds its lyrics client itself; tests that want one pass a fake explicitly
    monkeypatch.setattr(noaap.service, "Lrclib", lambda *a, **k: NoLyrics())
    # **no test leaves a timer running that tidies up the graphics card** (§9, slice 84). Every `App`
    # starts one watcher thread; it outlives the test that made it, and when its window passes it
    # calls `release_gpu_memory()` — which reached into whichever *later* test had stubbed
    # `sys.modules` with a fake torch and made `test_releasing_empties_the_pool_when_torch_is_here`
    # count three calls instead of one on CI. Measured before this line: **59** live `noaap-card-idle`
    # threads after three test files. So the one line that starts such a thread does nothing here; a
    # test that is *about* the watcher replaces the same hook with its own collector and drives the
    # loop by hand.
    monkeypatch.setattr("noaap.timing._in_the_background", lambda watch: None)
    return started

@pytest.fixture(scope="session")
def one_second_of_sound(tmp_path_factory) -> Path:
    """A second of a sine tone as Opus — enough audio for a real model to be asked to load.

    Here rather than in one test file because two of the opt-in live tests want it (and one of them
    asked for a fixture that lived in another file and therefore could never run at all).
    """
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path_factory.mktemp("tone") / "tone.opus"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", "libopus", str(path)], check=True)
    return path


@pytest.fixture(scope="session")
def one_second_of_mp3(tmp_path_factory) -> Path:
    """The same second of tone as **MP3** — the format the fixtures here did not have.

    Everything in this suite was Opus, and the user's own collection is mostly mp3. Three faults in
    the take-in were mp3-only and none of them could be reproduced here: a temporary copy that lost
    its suffix (so every mp3 was opened as an Ogg), a restore that could not find a retagged file
    (an ID3 rewrite changes the size; a padded Ogg comment does not), and a search whose one guess
    was that very size. A test that means to be about somebody's own music uses this one.
    """
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path_factory.mktemp("tone") / "tone.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", "libmp3lame", str(path)], check=True)
    if not path.is_file():
        pytest.skip("this ffmpeg cannot write mp3")
    return path
