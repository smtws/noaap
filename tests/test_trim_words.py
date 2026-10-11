"""A cut never deletes words and never asks LRCLIB (§9, slice 169; R-579, finding 11).

The P120 walk trimmed `Ever Dream` to 28 s, the same pass asked LRCLIB for a recording of that length,
found none, and unlinked the 1,960-character synced sidecar. Now the words follow the cut."""

import shutil

from test_incremental import FakeYouTube, opus_template, vol1
from test_trim import duration, tone

from noaap.download import load_plan, run, save_plan
from noaap.lyrics import read_sidecar, sidecar_path, write_sidecar
from noaap.models import Provenance
from noaap.plan import build_plan
from noaap.trim import apply, original_path

SYNCED = "[00:01.00] one\n[00:03.50] two\n[00:06.00] three\n[00:09.00] four"


class NeverAsked:
    """An LRCLIB that fails the test if anything asks it."""

    def get(self, *args, **kwargs):
        raise AssertionError("a cut asked LRCLIB")


def an_album(tmp_path, opus_template, words):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album = tmp_path / plan.folder
    run(plan, album, yt)
    plan = load_plan(album)
    track = plan.tracks[0]
    write_sidecar(album, track, words)
    track.lyrics = "synced" if words.startswith("[") else "plain"
    for other in plan.tracks[1:]:
        other.lyrics = "none"          # looked up already: only the cut track is in question
    save_plan(plan, album)
    return yt, album


def cut(yt, album, start, end):
    plan = load_plan(album)
    track = plan.tracks[0]
    track.trim_start, track.trim_end = start, end
    run(plan, album, yt, lyrics=NeverAsked())
    plan = load_plan(album)
    return plan.tracks[0]


def test_a_cut_moves_the_stamps_and_a_reset_puts_them_back(tmp_path, opus_template):
    yt, album = an_album(tmp_path, opus_template, SYNCED)
    track = load_plan(album).tracks[0]
    before = sidecar_path(album, track.filename).read_bytes()

    track = cut(yt, album, 0.2, 0.6)
    assert read_sidecar(album, track) is not None, "no sidecar is ever unlinked by a trim"
    assert read_sidecar(album, track).splitlines()[0] == "[00:00.40] one"   # 1.0 − 0.2, held at 0.4
    assert track.lyrics == "synced"
    assert original_path(album, track).with_suffix(".lrc").read_bytes() == before

    track = cut(yt, album, 0.3, 0.6)                 # a second cut: from the kept words, not the cut ones
    assert read_sidecar(album, track).splitlines()[0] == "[00:00.30] one"

    track = cut(yt, album, None, None)               # reset
    assert sidecar_path(album, track.filename).read_bytes() == before, "back byte for byte"


def test_a_line_before_the_cut_is_dropped(tmp_path, opus_template):
    yt, album = an_album(tmp_path, opus_template, SYNCED)
    track = cut(yt, album, 0.5, None)
    lines = read_sidecar(album, track).splitlines()
    assert lines[0] == "[00:00.50] one" and len(lines) == 4
    from noaap.lyrics import shifted_lrc
    assert shifted_lrc(SYNCED, 2.0, None).splitlines() == ["[00:01.50] two", "[00:04.00] three", "[00:07.00] four"]


def test_plain_words_are_left_as_they_are(tmp_path, opus_template):
    yt, album = an_album(tmp_path, opus_template, "one\ntwo\nthree")
    before = sidecar_path(album, load_plan(album).tracks[0].filename).read_bytes()
    track = cut(yt, album, 0.2, 0.6)
    assert sidecar_path(album, track.filename).read_bytes() == before
    track = cut(yt, album, None, None)
    assert sidecar_path(album, track.filename).read_bytes() == before


def test_your_own_words_stay_yours_through_a_cut(tmp_path, opus_template):
    yt, album = an_album(tmp_path, opus_template, SYNCED)
    plan = load_plan(album)
    plan.tracks[0].provenance["lyrics"] = Provenance.USER
    save_plan(plan, album)
    track = cut(yt, album, 0.2, None)
    assert track.provenance.get("lyrics") == Provenance.USER
    assert read_sidecar(album, track).splitlines()[0] == "[00:00.80] one"


def test_words_edited_after_a_cut_are_what_the_next_cut_starts_from(tmp_path, opus_template):
    yt, album = an_album(tmp_path, opus_template, SYNCED)
    track = cut(yt, album, 0.2, None)
    here = sidecar_path(album, track.filename)
    here.write_text(here.read_text().replace("one", "ONE"))   # the person fixes a word on the cut file
    track = cut(yt, album, 0.3, None)
    assert read_sidecar(album, track).splitlines()[0] == "[00:00.70] ONE"


def test_no_words_no_kept_words(tmp_path, tone):
    plan = build_plan(vol1())
    track = plan.tracks[0]
    shutil.copy(tone, tmp_path / "t.opus")
    track.filename = "t.opus"
    track.trim_start = 2.0
    apply(tmp_path, track, tmp_path / "t.opus")
    assert not original_path(tmp_path, track).with_suffix(".lrc").exists()
