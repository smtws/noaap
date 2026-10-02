"""Asking the rule again about copies it has already listed (DESIGN §9, slice 78).

A rule can change — the user changed one in P63 — and every copy a pass listed was judged under the
old one. They carry their own numbers, so the question can be asked again **without opening a single
audio file**, and that is the whole design: read the plans, judge, print, and touch nothing unless
somebody says `--apply`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from noaap.download import load_plan, save_plan
from noaap.merge import carry_out_rejudged, facts_of, rejudge
from noaap.models import AlbumPlan, Candidate, Kind, PlanTrack, Provenance
from noaap.ranking import Verdict


def a_library(tmp_path: Path, *, mine: dict | None = None, theirs: dict | None = None,
              track_over: dict | None = None) -> tuple[Path, Path, AlbumPlan]:
    """One album, one track, one copy on the list — with the numbers a pass would have recorded."""
    library = tmp_path / "library"
    album_dir = library / "A Band" / "An Album"
    album_dir.mkdir(parents=True)
    (album_dir / "01 A Song.opus").write_bytes(b"the file that is here")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "01 A Song.flac").write_bytes(b"the copy on the list")

    track = PlanTrack(video_id="aaaaaaaaaaa", number=1, artist="A Band", title="A Song",
                      filename="01 A Song.opus", provenance={}, state="done", ext="opus")
    track.candidates = [
        Candidate(ref="aaaaaaaaaaa", provider="youtube", added_by="source",
                  **{"length": 200.0, "codec": "opus", "bitrate": 128_000, "cutoff_khz": 20,
                     "full_band": False, "bytes": 3_000_000, **(mine or {})}),
        Candidate(ref=str(elsewhere / "01 A Song.flac"), provider="folder", added_by="pass",
                  undecided=True, why="both are lossless or neither is",
                  **{"length": 200.0, "codec": "flac", "bitrate": 900_000, "cutoff_khz": 20,
                     "full_band": False, "bytes": 24_000_000, **(theirs or {})}),
    ]
    track.chosen = "aaaaaaaaaaa"
    for key, value in (track_over or {}).items():
        setattr(track, key, value)
    plan = AlbumPlan(source_url="https://y/1", source_id="p1", kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=None,
                     folder="A Band/An Album", tracks=[track])
    save_plan(plan, album_dir)
    return library, album_dir, plan


def test_a_copy_the_old_rule_could_not_place_is_placed_by_the_new_one(tmp_path):
    """The case the user asked for: a lossless copy that gives up nothing, listed as undecided under
    the rule that said a container decides nothing."""
    library, _, _ = a_library(tmp_path)

    changes = rejudge(library)

    assert len(changes) == 1 and changes[0].changed
    assert changes[0].verdict.verdict is Verdict.REPLACE
    assert "gives up nothing" in changes[0].verdict.why
    assert "undecided → replace" in changes[0].line


def test_and_one_the_new_rule_still_cannot_place_produces_no_line(tmp_path):
    """A narrower lossless copy wins nothing, so the answer is the same answer: nothing to say."""
    library, _, _ = a_library(tmp_path, theirs={"cutoff_khz": 19})

    changes = rejudge(library)

    assert len(changes) == 1 and not changes[0].changed and not changes[0].left_alone


def test_a_pair_with_a_number_missing_is_left_alone_and_counted(tmp_path):
    """An absent number is not a bad one: re-measuring means decoding both files, which is `merge`'s
    job and not this pass's."""
    library, _, _ = a_library(tmp_path, theirs={"cutoff_khz": None})
    left = rejudge(library)[0]
    assert left.left_alone and "no recorded measurement" in left.left_alone
    assert "left alone" in left.line

    library2, _, _ = a_library(tmp_path / "second", mine={"length": None})
    assert "the copy here" in rejudge(library2)[0].left_alone


@pytest.mark.parametrize("work,reason", [
    ({"trim_start": 5.0}, "you trimmed this one"),
    ({"source_override": "aaaaaaaaaaa"}, "you chose where this one comes from"),
    ({"provenance": {"lyrics": Provenance.USER}, "lyrics": "synced"}, "your own words are timed"),
])
def test_a_track_the_user_worked_on_stays_untouched_with_the_reason_printed(tmp_path, work, reason):
    library, _, _ = a_library(tmp_path, track_over=work)

    change = rejudge(library)[0]

    assert change.left_alone and reason in change.left_alone
    assert not change.changed and reason in change.line


def test_a_copy_the_user_already_took_is_not_asked_about(tmp_path):
    """Taking one makes it the track's audio — `source_override`, from which `chosen` is derived —
    and `undecided_copies` stops listing it, so there is no pair to judge and no line to print."""
    library, album_dir, plan = a_library(tmp_path)
    taken = plan.tracks[0].candidates[1].ref
    plan.tracks[0].source_override = plan.tracks[0].chosen = taken
    save_plan(plan, album_dir)

    assert load_plan(album_dir).tracks[0].effective_id == taken
    assert rejudge(library) == []


def test_a_refused_copy_stays_refused(tmp_path):
    library, album_dir, plan = a_library(tmp_path)
    plan.tracks[0].refused_candidates = [plan.tracks[0].candidates[1].ref]
    save_plan(plan, album_dir)

    assert rejudge(library) == []


def test_the_scope_can_be_one_artist_or_one_album(tmp_path):
    library, _, _ = a_library(tmp_path)

    assert len(rejudge(library, artist="A Band")) == 1
    assert rejudge(library, artist="Somebody Else") == []
    assert len(rejudge(library, album="An Album")) == 1
    assert rejudge(library, album="Another Album") == []


def test_judging_again_opens_no_audio_file(tmp_path, monkeypatch):
    """The numbers were written down when the pass measured them; this reads plans and nothing else."""
    from noaap import merge as merge_pass

    library, _, _ = a_library(tmp_path)
    monkeypatch.setattr(merge_pass, "consider", lambda *a, **k: pytest.fail("a file was measured"))
    monkeypatch.setattr("noaap.ranking.examine", lambda *a, **k: pytest.fail("a file was opened"))

    assert len(rejudge(library)) == 1


def test_apply_takes_the_copy_and_bins_what_was_there(tmp_path):
    """What a merge does with a `replace`, and nothing more."""
    library, album_dir, _ = a_library(tmp_path)
    changes = rejudge(library)

    done = carry_out_rejudged(changes, library)

    assert done == {"replaced": 1, "settled": 0, "failed": 0}
    after = load_plan(album_dir).tracks[0]
    assert after.filename.endswith(".flac") and (album_dir / after.filename).is_file()
    assert (album_dir / after.filename).read_bytes() == b"the copy on the list"
    assert not (album_dir / "01 A Song.opus").exists(), "the displaced file is gone from the album"
    assert list((library / ".recycle").rglob("*.opus")), "…and is in the bin"
    assert after.chosen == after.candidates[1].ref


def test_a_dry_run_changes_nothing(tmp_path):
    import hashlib

    library, album_dir, _ = a_library(tmp_path)
    plan_file = album_dir / ".noaap.json"
    before = hashlib.sha256(plan_file.read_bytes()).hexdigest()

    rejudge(library)

    assert hashlib.sha256(plan_file.read_bytes()).hexdigest() == before
    assert (album_dir / "01 A Song.opus").is_file()


def test_a_keep_stops_the_copy_being_offered_and_deletes_nothing(tmp_path):
    """The other direction: under the current rule this copy is not worth offering. Saying so is the
    whole action — the copy stays listed, with the sentence that settled it."""
    library, album_dir, _ = a_library(tmp_path, mine={"cutoff_khz": 22, "codec": "flac",
                                                      "bitrate": 900_000},
                                      theirs={"cutoff_khz": 17, "codec": "mp3", "bitrate": 128_000})
    changes = rejudge(library)
    assert changes[0].verdict.verdict is Verdict.KEEP

    done = carry_out_rejudged(changes, library)

    assert done["settled"] == 1 and done["replaced"] == 0
    after = load_plan(album_dir).tracks[0]
    assert (album_dir / "01 A Song.opus").is_file(), "nothing was taken"
    listed = after.candidate(changes[0].copy.ref)
    assert listed is not None and not listed.undecided and "holds more audio" in listed.why
    assert after.undecided_copies() == [], "and it is no longer waiting for anybody"


def test_the_command_is_dry_by_default_and_says_what_it_found(tmp_path, monkeypatch, capsys):
    """Like every merge: it prints and changes nothing until somebody says `--apply`."""
    import hashlib

    from noaap.cli import main

    library, album_dir, _ = a_library(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    before = hashlib.sha256((album_dir / ".noaap.json").read_bytes()).hexdigest()

    assert main(["merge", "--rejudge", "--library", str(library)]) == 0

    said = capsys.readouterr().out
    assert "undecided → replace" in said and "A Song" in said
    assert "1 listed copy asked again" in said and "1 would become replace" in said
    assert "nothing was changed" in said
    assert hashlib.sha256((album_dir / ".noaap.json").read_bytes()).hexdigest() == before

    assert main(["merge", "--rejudge", "--apply", "--library", str(library)]) == 0
    assert "1 replaced" in capsys.readouterr().out
    assert load_plan(album_dir).tracks[0].filename.endswith(".flac")


def test_the_command_needs_a_source_or_the_flag(tmp_path, monkeypatch, capsys):
    from noaap.cli import main

    library, _, _ = a_library(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    assert main(["merge", "--library", str(library)]) == 2
    assert "--rejudge" in capsys.readouterr().err
