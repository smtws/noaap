"""Every test runs isolated: own cache/config dirs, no PO-token server, no lrclib.net."""

import os
import shutil
import subprocess
from pathlib import Path

# **no card for the test process, before anything can import torch** (§9, slice 183; P131). Nine
# tests reached the real torch's CUDA through `release_gpu_memory()`, `resolved_device()` or
# `card_room()` once an earlier test had imported it; with no device visible the driver is never
# opened (measured: no `/dev/nvidia*`, `is_available()` False). `never_the_card` below checks it.
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import pytest

import noaap.download
import noaap.pot
import noaap.service


class NoLyrics:
    """What a Service gets instead of a real lrclib client: nothing is found, nothing is asked."""

    def get(self, artist, title, album=None, length=None, skip=()):
        return None

    def by_id(self, lrclib_id):
        return None


def cards_open() -> set[str]:
    """The graphics card's device files this process holds (§9, slice 183).

    Once a process has made a CUDA context the driver keeps `/dev/nvidia*` open until it exits, so a
    test that touched the card leaves this behind, whichever library did it — torch, ctranslate2,
    onnxruntime. Asking torch would see only torch.
    """
    found = set()
    for fd in os.listdir("/proc/self/fd") if os.path.isdir("/proc/self/fd") else ():
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if target.startswith("/dev/nvidia"):
            found.add(target)
    return found


@pytest.fixture(autouse=True)
def never_the_card(request):
    """**No unit test opens the graphics card** (§9, slice 183; P131). One did, through
    `release_gpu_memory()` with the real torch already imported by an earlier test: a CUDA context of
    a few hundred MB that stayed with the test process, on the card the user's service runs on."""
    before = cards_open()
    yield
    if opened := cards_open() - before:
        pytest.fail(f"{request.node.nodeid} opened the graphics card: {', '.join(sorted(opened))}")


def pytest_sessionfinish(session, exitstatus):
    """And the run as a whole: a card held at the end fails it, whoever opened it."""
    if held := cards_open():
        session.config.pluginmanager.get_plugin("terminalreporter").write_line(
            f"the test process holds the graphics card: {', '.join(sorted(held))}", red=True)
        session.exitstatus = 1


REAL_ARCHIVE_COVER = noaap.download._archive_cover   # for the tests about that function itself


def _no_archive(url, why=None):
    if why is not None and "coverartarchive.org" in url:
        why.append("coverartarchive.org answered 404 (a test)")


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))   # the server's job table (§9, slice 184)
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
    # **no test reaches the Cover Art Archive** (§9, slices 181–182): neither the check's HEAD nor
    # the fetch. The two agree — nothing answers — unless a test says otherwise. A fetch that went
    # out for real made a test pass alone and fail in the suite, whenever the archive was slow.
    monkeypatch.setattr("noaap.download.caa_answers", lambda url: "coverartarchive.org answered 404 (a test)")
    monkeypatch.setattr("noaap.download._archive_cover", _no_archive)
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
    try:    # an ffmpeg without libmp3lame is a skip, not an error: CI's may be built without it
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                        "-c:a", "libmp3lame", str(path)], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        pytest.skip(f"this ffmpeg cannot write mp3: {e.stderr.decode(errors='replace')[:120]}")
    if not path.is_file():
        pytest.skip("this ffmpeg wrote no mp3")
    return path
