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
