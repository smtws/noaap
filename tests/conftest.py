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
