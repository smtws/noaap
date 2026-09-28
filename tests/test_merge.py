"""The merge pass: measure both libraries, propose, and change nothing (DESIGN §9, slice 54).

A bare `merge` is a read. These cases hold it to that, and to the other half of its job — that
what it prints is checkable, because a verdict nobody can disagree with is an instruction.
"""
from __future__ import annotations

import json
from pathlib import Path

from noaap.download import save_plan
from noaap.merge import Survey, describe, report, survey, unpaired_albums
from noaap.models import AlbumPlan, Kind, PlanTrack
from noaap.ranking import Facts, Judgement, Verdict
from noaap.spectrum import Spectrum


def plan_with(folder: str, *titles: str, provider: str = "youtube", mbid: str | None = None) -> AlbumPlan:
    tracks = [PlanTrack(video_id=f"{folder}-{n}", number=n, artist="A Band", title=t,
                        filename=f"{n:02d} - {t}.opus", provenance={}, mbid=mbid)
              for n, t in enumerate(titles, 1)]
    return AlbumPlan(source_url=f"x://{folder}", source_id=folder, kind=Kind.OFFICIAL_ALBUM,
                     album=folder, albumartist="A Band", year=None, cover_url=None,
                     folder=f"A Band/{folder}", tracks=tracks, provider=provider)


def library(root: Path, *plans: AlbumPlan) -> Path:
    for plan in plans:
        album_dir = root / plan.folder
        save_plan(plan, album_dir)
        for track in plan.tracks:
            (album_dir / track.filename).write_bytes(b"audio" * 100)
    return root


def verdict(kind: Verdict, why: str = "because", size: int = 5_000_000) -> Judgement:
    band = Spectrum(cutoff=22, full=True, why="at this file's ceiling")
    new = Facts(length=200.0, band=band, codec="flac", bitrate=900_000, bytes=size, lossless=True)
    old = Facts(length=200.0, band=Spectrum(cutoff=20, why="band-limited"), codec="opus",
                bitrate=128_000, bytes=size // 5)
    return Judgement(kind, why, new, old)


def fixed(kind: Verdict, **kw):
    return lambda pair: verdict(kind, **kw)


# -- a bare merge is a read --------------------------------------------------------------------------


def test_nothing_is_written_by_a_survey(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One", "Two"))
    target = library(tmp_path / "target", plan_with("Album", "One", "Two"))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(target.rglob("*")) if p.is_file()}

    survey(source, target, judge=fixed(Verdict.REPLACE))

    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(target.rglob("*")) if p.is_file()} == before


def test_the_report_ends_by_saying_so(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One"))
    target = library(tmp_path / "target", plan_with("Album", "One"))

    lines = report(survey(source, target, judge=fixed(Verdict.REPLACE)))

    assert lines[-1] == "nothing was changed. `noaap merge --apply` does it."


# -- what it concluded -------------------------------------------------------------------------------


def test_every_pair_gets_a_verdict(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One", "Two", "Three"))
    target = library(tmp_path / "target", plan_with("Album", "One", "Two", "Three"))

    found = survey(source, target, judge=fixed(Verdict.REPLACE))

    assert found.counts() == {"replace": 3, "fill": 0, "keep": 0, "undecided": 0}
    assert len(found.acting) == 3


def test_only_replace_and_fill_move_bytes(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One", "Two"))
    target = library(tmp_path / "target", plan_with("Album", "One", "Two"))

    acting = survey(source, target, judge=fixed(Verdict.REPLACE))
    quiet = survey(source, target, judge=fixed(Verdict.UNDECIDED))

    assert acting.bytes_moved() == (10_000_000, 2_000_000)
    assert quiet.bytes_moved() == (0, 0), "an undecided proposal moves nothing, not even on paper"


def test_the_albums_of_this_library_are_how_it_is_grouped(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One", "Two"))
    target = library(tmp_path / "target", plan_with("First", "One"), plan_with("Second", "Two"))

    found = survey(source, target, judge=fixed(Verdict.REPLACE))

    assert sorted(d.name for d in found.albums()) == ["First", "Second"]


def test_what_the_library_does_not_have_is_offered_by_album(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere", "Also Elsewhere"))
    target = library(tmp_path / "target", plan_with("Album", "One"))

    found = survey(source, target, judge=fixed(Verdict.KEEP))
    offered = unpaired_albums(found)

    assert [len(v) for v in offered.values()] == [2]
    assert sorted(s.track.title for v in offered.values() for s in v) == ["Also Elsewhere", "Elsewhere"]


# -- what a person can check ---------------------------------------------------------------------------


def test_a_proposal_shows_both_files_numbers(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One"))
    target = library(tmp_path / "target", plan_with("Album", "One"))

    found = survey(source, target, judge=fixed(Verdict.REPLACE, why="holds more audio"))
    lines = describe(found.proposals[0])

    assert "replace" in lines[0] and "A Band - One" in lines[0]
    assert lines[1].strip() == "holds more audio"
    assert "here:" in lines[2] and "opus" in lines[2] and "to 20 kHz" in lines[2]
    assert "incoming:" in lines[3] and "flac" in lines[3] and "all it can hold" in lines[3]


def test_a_fill_says_there_was_nothing_here():
    from noaap.pairing import How, Pair, Side

    empty = Judgement(Verdict.FILL, "nothing here", Facts(length=200.0, bytes=1), None)
    plan = plan_with("Album", "One")
    side = Side(plan, plan.tracks[0], Path("/library"))
    lines = describe(type("P", (), {"pair": Pair(side, side, How.NAME), "verdict": empty,
                                    "album": plan, "album_dir": Path("/library")})())

    assert "here:     nothing" in lines[2]


def test_the_totals_are_printed_even_when_nothing_would_move(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One"))
    target = library(tmp_path / "target", plan_with("Album", "One"))

    lines = report(survey(source, target, judge=fixed(Verdict.KEEP)))

    assert any("0 to replace" in line and "1 to keep" in line for line in lines)
    assert any("nothing would be added and nothing binned" in line for line in lines)


def test_keeps_are_not_listed_by_default_but_are_counted(tmp_path):
    """An album where nothing changes is noise in a report about changes."""
    source = library(tmp_path / "source", plan_with("Intake", "One"))
    target = library(tmp_path / "target", plan_with("Album", "One"))

    lines = report(survey(source, target, judge=fixed(Verdict.KEEP)))

    assert not any(line.startswith("A Band — Album") for line in lines)
    assert any("1 to keep" in line for line in lines)


def test_an_empty_survey_still_reports(tmp_path):
    source = library(tmp_path / "source")
    target = library(tmp_path / "target")

    lines = report(survey(source, target))

    assert any("0 to replace" in line for line in lines)


def test_a_survey_can_be_read_back_as_data(tmp_path):
    """The page and the tests want the same thing the report renders, not its text."""
    source = library(tmp_path / "source", plan_with("Intake", "One"))
    target = library(tmp_path / "target", plan_with("Album", "One"))

    found: Survey = survey(source, target, judge=fixed(Verdict.REPLACE))
    p = found.proposals[0]

    assert json.dumps({"verdict": p.verdict.verdict.value, "why": p.verdict.why,
                       "new": p.verdict.new.line(), "old": p.verdict.old.line()})


# -- and what `--apply` does ---------------------------------------------------------------------------


def real_library(root: Path, folder: str, *titles: str, ext: str = "opus", provider: str = "youtube",
                 body: bytes = b"old audio") -> tuple[Path, AlbumPlan]:
    tracks = [PlanTrack(video_id=f"{folder}-{n}", number=n, artist="A Band", title=t,
                        filename=f"{n:02d} - {t}.{ext}", provenance={}, file_length=200.0)
              for n, t in enumerate(titles, 1)]
    plan = AlbumPlan(source_url=f"x://{folder}", source_id=folder, kind=Kind.OFFICIAL_ALBUM,
                     album=folder, albumartist="A Band", year=None, cover_url=None,
                     folder=f"A Band/{folder}", tracks=tracks, provider=provider)
    album_dir = root / plan.folder
    save_plan(plan, album_dir)
    for track in plan.tracks:
        (album_dir / track.filename).write_bytes(body)
    return root, plan


def test_apply_copies_the_file_in_and_bins_what_was_there(tmp_path):
    from noaap.merge import carry_out
    from noaap.recycle import entries as bin_entries

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder",
                             body=b"new audio")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    found = survey(source, target, judge=fixed(Verdict.REPLACE, why="holds more audio"))
    done = carry_out(found, target)

    assert done == {"replaced": 1, "filled": 0, "failed": 0}
    album_dir = target / plan.folder
    assert (album_dir / "A Band - Album - 01 - One.flac").read_bytes() == b"new audio", \
        "the file decides its extension, and the album decides its name"
    assert not (album_dir / "01 - One.opus").exists(), "what was there went to the bin"
    binned = bin_entries(target)
    assert len(binned) == 1 and "replaced by a copy from folder" in binned[0].reason


def test_the_bin_entry_holds_both_files_numbers(tmp_path):
    import json as _json

    from noaap.merge import carry_out
    from noaap.recycle import entries as bin_entries

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, _ = real_library(tmp_path / "target", "Album", "One")

    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE)), target)

    entry = bin_entries(target)[0]
    ranking = _json.loads((entry.path / "bin.json").read_text())["ranking"]
    assert ranking["chosen"]["ref"] == "Intake-1"
    assert ranking["chosen"]["codec"] == "flac" and ranking["chosen"]["cutoff_khz"] == 22
    assert ranking["displaced"]["codec"] == "opus" and ranking["displaced"]["cutoff_khz"] == 20


def test_the_plan_records_where_the_audio_now_comes_from(tmp_path):
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE, why="holds more audio")), target)

    track = load_plan(target / plan.folder).tracks[0]
    assert track.chosen == "Intake-1" and track.source_override == "Intake-1"
    taken = track.candidate("Intake-1")
    assert taken.provider == "folder" and taken.added_by == "pass" and taken.why == "holds more audio"
    assert taken.codec == "flac" and taken.bytes
    assert track.tagged is None, "the ordinary pass retags it with this album's names"


def test_restoring_marks_the_displacer_refused(tmp_path):
    """The user has just said they preferred what was here, so it is never offered again."""
    from noaap.config import Config
    from noaap.download import load_plan
    from noaap.merge import carry_out
    from noaap.recycle import entries as bin_entries
    from noaap.service import Service

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")
    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE)), target)

    service = Service(Config(musicbrainz=False, lyrics=False), target, log=lambda s: None)
    service.restore(bin_entries(target)[0].id)

    track = load_plan(target / plan.folder).tracks[0]
    assert "Intake-1" in track.refused_candidates


def test_a_source_file_that_vanished_fails_only_its_own_track(tmp_path):
    from noaap.merge import carry_out

    source, source_plan = real_library(tmp_path / "source", "Intake", "One", "Two",
                                       ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One", "Two")
    found = survey(source, target, judge=fixed(Verdict.REPLACE))
    (source / source_plan.folder / "01 - One.flac").unlink()

    done = carry_out(found, target)

    assert done == {"replaced": 1, "filled": 0, "failed": 1}
    assert (target / plan.folder / "A Band - Album - 02 - Two.flac").is_file()


def test_a_replacement_of_a_different_length_makes_the_timings_stale(tmp_path):
    """R-164, ruling 5: after a replacement the existing check must flag words stamped against
    the file that is gone."""
    from noaap.download import load_plan
    from noaap.lyrics import timings_stale
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")
    timed = load_plan(target / plan.folder)
    timed.tracks[0].lyrics = "synced"
    timed.tracks[0].lyrics_for_source = timed.tracks[0].video_id
    timed.tracks[0].lyrics_for_length = 200.0
    save_plan(timed, target / plan.folder)

    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE)), target)

    after = load_plan(target / plan.folder).tracks[0]
    assert timings_stale(after), "the words were timed against the file that just went to the bin"


def test_restoring_puts_the_old_file_back_and_removes_the_new_one(tmp_path):
    """A restore after a replacement is an undo, not a second copy in the folder."""
    from noaap.config import Config
    from noaap.download import load_plan
    from noaap.merge import carry_out
    from noaap.recycle import entries as bin_entries
    from noaap.service import Service

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder",
                             body=b"new audio")
    target, plan = real_library(tmp_path / "target", "Album", "One", body=b"old audio")
    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE)), target)
    album_dir = target / plan.folder

    Service(Config(musicbrainz=False, lyrics=False), target,
            log=lambda s: None).restore(bin_entries(target)[0].id)

    audio = sorted(p.name for p in album_dir.iterdir() if p.suffix in (".opus", ".flac"))
    assert audio == ["A Band - Album - 01 - One.opus"], "one file, and it is the one that came back"
    assert (album_dir / audio[0]).read_bytes() == b"old audio"
    track = load_plan(album_dir).tracks[0]
    assert track.source_override is None and track.chosen == track.video_id
    assert "Intake-1" in track.refused_candidates


def test_a_merged_track_keeps_saying_where_its_audio_came_from(tmp_path):
    """`own_the_candidates` used to claim whatever `source_override` named, which rewrote a
    merged-in track's provider to the album's on the next load — so a re-download would have asked
    YouTube for a folder path."""
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE)), target)
    reloaded = load_plan(target / plan.folder)

    assert reloaded.provider == "youtube", "the album is still YouTube's"
    assert reloaded.tracks[0].candidate("Intake-1").provider == "folder"
    assert reloaded.tracks[0].provider_in(reloaded) == "folder", "and its audio is the folder's"
