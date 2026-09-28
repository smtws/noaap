"""The corpus again, end to end, on real audio — opt-in, slow, and nobody's default.

`tests/test_regression_corpus.py` asserts what the measurements decided, from recordings. This file
asserts the same outcomes by **doing the work again**: the real local provider, real tracks, no
fixtures. It is the only thing that can catch a change that leaves the recorded numbers untouched
and the software broken — a model version bump, a resampling change, a device default.

Nothing here reads from the repository and nothing is committed with it. Audio stays where it is,
is copied read-only into pytest's own temp directory for the run, and is deleted with it.

    YTALBUM_CORPUS_AUDIO=1 uv run pytest -m slow -v

and, when the library is not the one in your ytalbum config:

    YTALBUM_CORPUS_AUDIO=1 YTALBUM_CORPUS_LIBRARY=~/Music/Yours uv run pytest -m slow -v

**Pointing it at your own library.** It looks in the `library_root` from your ytalbum config and
finds each track by artist, title and album through the album plans — so it works unchanged on any
library that holds these recordings, and skips with a reason on one that does not. To use different
tracks, change the `Case` entries below: each names an artist, a title, an album and the outcome the
measurement recorded. A case whose track is missing skips; it never fails for absence, because a
library not containing somebody else's music is not a regression.
"""
from __future__ import annotations

import json
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from noaap.lyrics import fit_verdict
from noaap.timing import LOST_SPAN

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("YTALBUM_CORPUS_AUDIO"),
                       reason="set YTALBUM_CORPUS_AUDIO=1 to run the corpus against real audio"),
]


@dataclass(frozen=True)
class Case:
    artist: str
    title: str
    album: str
    why: str


BLACKBEARD = Case("Mr. Hurley & Die Pulveraffen", "Blackbeard", "S.O.S.",
                  "the near-miss that started P35: 4.4 s outside the rule, and the song's own words")
BERZERKERMODE = Case("Feuerschwanz", "Berzerkermode", "Fegefeuer",
                     "LRCLIB's stamps are ~6.4 s early; the aligner puts them right")
ARGENT = Case("Lord of the Lost", "Argent", "Judas",
              "the whole-track case: the second method is 120 s out and must be the one named")
TRIPPER = Case("Kupfergold", "Und 'n Tripper", "Fasan Alarm",
               "a trimmed track, where the player's clock and the file's are not the same")


def _recorded() -> list[dict]:
    """The P35 rows, for the entry ids the measurement actually used."""
    path = Path(__file__).parent / "fixtures" / "lrclib_near_misses.json"
    return json.loads(path.read_text(encoding="utf-8"))["candidates"]


def _plans(root: Path):
    for path in sorted(root.rglob(".ytalbum.json")):
        try:
            yield path.parent, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue


def library() -> Path:
    """Where the music is.

    `tests/conftest.py` redirects `XDG_CONFIG_HOME` for every test so that nothing reads the real
    configuration by accident. That protection is right and this suite does not defeat it: it takes
    the library from **`YTALBUM_CORPUS_LIBRARY`** when that is set, and otherwise reads the config
    file deliberately, from the real home directory, saying so here rather than reaching around the
    fixture in silence.
    """
    if named := os.environ.get("YTALBUM_CORPUS_LIBRARY"):
        return Path(named).expanduser()
    path = Path.home() / ".config" / "ytalbum" / "config.toml"
    if not path.is_file():
        pytest.skip(f"no {path} and no YTALBUM_CORPUS_LIBRARY: point this suite at a library")
    root = tomllib.loads(path.read_text(encoding="utf-8")).get("library_root")
    if not root:
        pytest.skip(f"{path} sets no library_root; set YTALBUM_CORPUS_LIBRARY instead")
    return Path(root).expanduser()


def find(case: Case) -> tuple[Path, dict]:
    """The album folder and plan track for a case, or skip saying exactly what is missing."""
    root = library()
    if not root.is_dir():
        pytest.skip(f"{root} is not a directory; set YTALBUM_CORPUS_LIBRARY")
    hits = []
    for album_dir, plan in _plans(root):
        if case.album.lower() not in str(plan.get("album", "")).lower():
            continue
        for track in plan.get("tracks", []):
            if (case.title.lower() in str(track.get("title", "")).lower()
                    and case.artist.lower() in str(track.get("artist", "")).lower()):
                hits.append((album_dir, track))
    # a track on two albums is the same recording twice; a zero-length copy is one that was never
    # downloaded, and picking it would fail for a reason that has nothing to do with the code
    usable = [(d, t) for d, t in hits
              if t.get("state") == "done" and (t.get("file_length") or t.get("duration"))]
    if not usable:
        pytest.skip(f"{case.artist} — {case.title} ({case.album}) is not in this library, "
                    f"or has nothing downloaded: {len(hits)} match(es), none usable")
    return usable[0]


def audio_of(album_dir: Path, track: dict, into: Path) -> Path:
    """A read-only copy of the track's audio inside the test's own temp directory."""
    name = track.get("filename")
    src = album_dir / name if name else None
    if not src or not src.is_file():
        pytest.skip(f"the plan names {name!r}, which is not on disk")
    out = into / src.name
    shutil.copy2(src, out)
    out.chmod(0o444)
    return out


def sidecar_of(album_dir: Path, track: dict) -> list[str]:
    name = track.get("filename")
    lrc = (album_dir / name).with_suffix(".lrc") if name else None
    if not lrc or not lrc.is_file():
        pytest.skip("no .lrc beside this track to align")
    from noaap.timing import plain_lines

    return plain_lines(lrc.read_text(encoding="utf-8"))


def provider(**kw):
    from noaap.timing_local import LocalTiming

    try:
        engine = LocalTiming(**kw)
        if "align" not in engine.capabilities():
            pytest.skip("the local provider cannot align here: the ytalbum[timing] extra is missing")
    except Exception as e:                                   # any import problem at all is a skip
        pytest.skip(f"the local provider is not usable here: {e}")
    return engine


# ── the cases ────────────────────────────────────────────────────────────────────────────

def test_blackbeard_the_entrys_words_fit_this_recording(tmp_path: Path) -> None:
    """P35's founding case, done again: LRCLIB's words for a file 4.4 s away really are its words."""
    album_dir, track = find(BLACKBEARD)
    audio = audio_of(album_dir, track, tmp_path)
    from noaap.lyrics import Lrclib
    from noaap.timing import plain_lines

    # one read-only lookup: this is the path the feature actually takes, and nothing is published
    try:
        found = Lrclib().candidates(track.get("artist", ""), track.get("title", ""))
    except Exception as e:                                   # offline, rate-limited, anything
        pytest.skip(f"lrclib could not be reached: {e}")
    # the recorded measurement used one specific entry; any other is a different measurement
    wanted = next(r["lrclib_id"] for r in _recorded() if r["title"] == BLACKBEARD.title)
    entry = next((c for c in found if c.lrclib_id == wanted), None)
    if not entry:
        pytest.skip(f"lrclib entry {wanted} is no longer offered for this track")
    lines = plain_lines(entry.text or "")
    timed = provider().align(audio, lines)

    starts = [line.start for line in timed.lines]
    # the provider records its own signals on every alignment (`own_*`); span needs the separated
    # voice, so it is the provider's to report, not something to recompute from the stamps alone
    span = float(timed.parameters["own_span"])
    unplaced = sum(1 for s in starts if s is None) / max(len(starts), 1)
    assert fit_verdict(span, unplaced) == "words+stamps", (
        f"span {span:.3f}, unplaced {unplaced:.0%} — the recording says these words fit")


def test_berzerkermode_the_aligner_corrects_an_entry_that_is_early(tmp_path: Path) -> None:
    """LRCLIB's first stamp reads 00:12.52; the singing starts about 6.4 s later."""
    album_dir, track = find(BERZERKERMODE)
    audio = audio_of(album_dir, track, tmp_path)
    timed = provider().align(audio, sidecar_of(album_dir, track))

    first = next((line.start for line in timed.lines if line.start is not None), None)
    assert first is not None, "nothing was placed at all"
    assert 17.0 < first < 21.0, (
        f"the first line landed at {first:.1f} s; the measurement put it near 19 s, which is "
        "LRCLIB's 12.5 s plus the 6.4 s correction")


def test_argent_the_second_method_is_the_one_named(tmp_path: Path) -> None:
    """Needs the check extra. The decoder is two minutes out on this track and must be the loser."""
    album_dir, track = find(ARGENT)
    engine = provider(verify=True)
    if not engine.verifying():
        pytest.skip("the ytalbum[timing-check] extra is not installed, so there is no second opinion")
    audio = audio_of(album_dir, track, tmp_path)
    timed = engine.align(audio, sidecar_of(album_dir, track))

    parameters = timed.parameters
    assert parameters.get("lost_method"), f"no method was named: {parameters}"
    assert "large-v3" in parameters["lost_method"], parameters["lost_method"]
    assert float(parameters.get("own_span", 1.0)) > LOST_SPAN
    assert all(line.start is not None for line in timed.lines), "every stamp is kept on a whole-track case"


def test_und_n_tripper_the_stamps_are_in_the_files_clock(tmp_path: Path) -> None:
    """P23's case. The track is not trimmed in the library today, so this applies its own trim to a
    scratch copy and checks the stamps count from the start of the *cut file*, not of the video."""
    album_dir, track = find(TRIPPER)
    whole = audio_of(album_dir, track, tmp_path)
    lines = sidecar_of(album_dir, track)

    import subprocess

    cut = tmp_path / "cut.opus"
    start = 30.0
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(whole),
                           "-c", "copy", str(cut)], capture_output=True, text=True)
    if done.returncode or not cut.is_file():
        pytest.skip(f"ffmpeg could not cut this track: {done.stderr.strip()[:120]}")

    engine = provider()
    before = engine.align(whole, lines)
    after = engine.align(cut, lines)
    pairs = [(a.start, b.start) for a, b in zip(before.lines, after.lines, strict=False)
             if a.start is not None and b.start is not None and a.start > start + 5]
    if len(pairs) < 5:
        pytest.skip("too few lines survive the cut to compare")
    shifts = [a - b for a, b in pairs]
    median = sorted(shifts)[len(shifts) // 2]
    assert abs(median - start) < 2.0, (
        f"the cut file's stamps are {median:.1f} s behind the whole file's; they should be {start:.0f} s, "
        "which is what 'the file's own clock' means")
