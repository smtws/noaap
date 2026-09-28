"""Where a file's audio stops, and what that is allowed to decide (DESIGN §9, slice 54).

The ladder is built here by ffmpeg — one wideband source encoded every way the reference collection
contains, including **a FLAC made from a lossy file**, which is the case the whole measurement
exists for: a lossless container proves nothing about what is inside it.

White noise is the source because it has content in every band. A sine would leave the bands above
14 kHz empty and the measurement would correctly answer "nothing up here to judge by" — which is
the right answer for that file and no test of anything else.

**What these cases deliberately do not assert: that 320 kbps keeps more than 128.** It does, for
music — measured over the reference collection, where mp3s at or below 160 kbps read 17 kHz and
those above 256 read 20. It does not for noise, because an encoder has no tonal masking to exploit
and spends its bits on bandwidth instead. A fixture that showed the ordering would have to be
tuned until it did, which would make it a test of the tuning. The ordering is evidence from the
collection and lives in the catalog; what belongs here is what holds for any audio at all.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from noaap.spectrum import Spectrum, better, measure

SECONDS = 55  # the window starts at 30 s, so the file has to be longer than that


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def ladder(tmp_path_factory) -> dict[str, Path]:
    """One wideband original, and the same audio through every encoder in the collection."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("ladder")
    out = {"original": d / "original.flac"}
    ffmpeg("-f", "lavfi", "-i", f"anoisesrc=d={SECONDS}:c=white:r=44100:a=0.5",
           "-c:a", "flac", str(out["original"]))
    for rate in (128, 192, 320):
        out[f"mp3 {rate}"] = d / f"mp3_{rate}.mp3"
        ffmpeg("-i", str(out["original"]), "-c:a", "libmp3lame", "-b:a", f"{rate}k", str(out[f"mp3 {rate}"]))
    out["opus"] = d / "opus.opus"
    ffmpeg("-i", str(out["original"]), "-c:a", "libopus", "-b:a", "128k", str(out["opus"]))
    # the two that matter: lossless containers holding lossy audio
    out["flac from mp3"] = d / "from_mp3.flac"
    ffmpeg("-i", str(out["mp3 128"]), "-c:a", "flac", str(out["flac from mp3"]))
    out["flac from opus"] = d / "from_opus.flac"
    ffmpeg("-i", str(out["opus"]), "-c:a", "flac", str(out["flac from opus"]))
    return out


# -- what the measurement says ---------------------------------------------------------------------


def test_the_lossless_original_reaches_its_own_ceiling(ladder):
    found = measure(ladder["original"])

    assert found.full is True and found.why == "at this file's ceiling"
    assert found.cutoff == 22, "44.1 kHz audio stops at 22.05 kHz because that is where it must"


@pytest.mark.parametrize("name", ["mp3 128", "mp3 192", "mp3 320", "opus"])
def test_every_lossy_encoder_leaves_a_mark(ladder, name):
    found = measure(ladder[name])

    assert found.known, f"{name} could not be measured at all"
    assert found.full is False, f"{name} reached the ceiling its lossless original reaches"
    assert found.cutoff < measure(ladder["original"]).cutoff


def test_a_lossless_container_does_not_hide_a_lossy_source(ladder):
    """The case the measurement exists for. A FLAC made from a 128 kbps mp3 is eight times the
    size of the mp3 and exactly as good; only the audio says so."""
    from_mp3 = measure(ladder["flac from mp3"])

    assert from_mp3.full is False
    assert from_mp3.cutoff == measure(ladder["mp3 128"]).cutoff


def test_a_flac_made_from_an_opus_reads_what_the_opus_reads(ladder):
    """Ruled in R-165: this is the file the intake will meet most often, because YouTube's audio
    re-encoded to FLAC is what half of a 24-bit/48 kHz collection turns out to be."""
    assert measure(ladder["flac from opus"]).cutoff == measure(ladder["opus"]).cutoff
    assert measure(ladder["flac from opus"]).full is False


def test_a_file_with_nothing_up_there_says_so_rather_than_scoring_badly(tmp_path):
    """A sine, a quiet acoustic take, an old master: there is no evidence here, and no evidence is
    never a reason to replace anything."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    quiet = tmp_path / "sine.flac"
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}:sample_rate=44100",
           "-c:a", "flac", str(quiet))

    found = measure(quiet)

    assert not found.known and found.why == "nothing up here to judge by"
    assert found.full is False


def test_something_that_is_not_audio_is_not_measurable(tmp_path):
    path = tmp_path / "sleeve.flac"
    path.write_bytes(b"<html>a consent page</html>")

    assert measure(path).why == "not measurable"


# -- what the measurement is allowed to decide -------------------------------------------------------


def test_reaching_its_own_ceiling_beats_a_bigger_number():
    """A CD rip stops at 22 kHz because 44.1 kHz audio stops there, not because anything was lost.
    A 48 kHz file band-limited at 21 kHz did lose something. Comparing the numbers first would
    read that backwards, which is the whole reason `full` exists."""
    cd_rip = Spectrum(cutoff=22, full=True)
    wider_but_cut = Spectrum(cutoff=21, full=False)

    assert better(cd_rip, wider_but_cut) == 1
    assert better(wider_but_cut, cd_rip) == -1


def test_two_files_at_their_ceilings_are_not_told_apart_by_this():
    assert better(Spectrum(cutoff=22, full=True), Spectrum(cutoff=24, full=True)) == 0


def test_a_kilohertz_decides_and_nothing_less_does():
    """R-164: wider by at least 1 kHz replaces; within 1 kHz is undecided."""
    assert better(Spectrum(cutoff=21, full=False), Spectrum(cutoff=20, full=False)) == 1
    assert better(Spectrum(cutoff=19, full=False), Spectrum(cutoff=21, full=False)) == -1
    assert better(Spectrum(cutoff=20, full=False), Spectrum(cutoff=20, full=False)) == 0


def test_an_unmeasured_file_never_wins_or_loses_by_being_unmeasured():
    """R-164's second rule, in the one place it would be easiest to get wrong."""
    assert better(Spectrum(), Spectrum(cutoff=17, full=False)) == 0
    assert better(Spectrum(cutoff=22, full=True), Spectrum()) == 0
    assert better(Spectrum(), Spectrum()) == 0


def test_a_tag_that_is_not_utf8_does_not_stop_the_measurement(tmp_path):
    """Found by running it: the profiling pass died on file 801 of 2000 with a UnicodeDecodeError.
    ffmpeg echoes the tags it reads, and this collection holds ID3 frames written in Latin-1, so
    `text=True` raised on a byte in a *comment*. A measurement that crashes on what a file says
    about itself is worse than one that cannot read it."""
    from mutagen.id3 import ID3, TIT2

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "latin1.mp3"
    ffmpeg("-f", "lavfi", "-i", f"anoisesrc=d={SECONDS}:c=pink:r=44100:a=0.8",
           "-c:a", "libmp3lame", "-b:a", "128k", str(path))
    tags = ID3(path)
    tags.add(TIT2(encoding=0, text=["Grüße aus München"]))  # encoding 0 is Latin-1
    tags.save(path)

    found = measure(path)

    assert found.known, "the file is perfectly readable; only its tag was not UTF-8"


def test_a_file_that_will_not_answer_does_not_stop_the_pass(tmp_path, monkeypatch):
    """A damaged file can keep ffmpeg busy for ever. A pass over thousands of files must not."""
    import subprocess

    from noaap import spectrum

    def hangs(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=kw.get("timeout", 0))

    monkeypatch.setattr(spectrum.subprocess, "run", hangs)

    found = measure(tmp_path / "whatever.flac")

    assert found.why == "not measurable" and not found.known
