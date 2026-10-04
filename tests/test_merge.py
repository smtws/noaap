"""The merge pass: measure both libraries, propose, and change nothing (DESIGN §9, slice 54).

A bare `merge` is a read. These cases hold it to that, and to the other half of its job — that
what it prints is checkable, because a verdict nobody can disagree with is an instruction.
"""
from __future__ import annotations

import json
from pathlib import Path

from noaap.download import PLAN_FILE, save_plan
from noaap.merge import Survey, describe, report, survey, unpaired_albums
from noaap.models import AlbumPlan, Kind, PlanTrack
from noaap.plan import wanted_folder
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

    assert done == {"replaced": 1, "filled": 0, "offered": 0, "failed": 0}
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

    assert done == {"replaced": 1, "filled": 0, "offered": 0, "failed": 1}
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


def test_both_candidates_keep_what_was_measured(tmp_path):
    """R-173: the bin entry had every number and the plan had none, so the panel could show
    nothing and the next pass would decode both files again."""
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE)), target)

    track = load_plan(target / plan.folder).tracks[0]
    taken, displaced = track.candidate("Intake-1"), track.candidate("Album-1")
    assert (taken.codec, taken.cutoff_khz, taken.full_band) == ("flac", 22, True)
    assert taken.length == 200.0 and taken.bytes
    assert (displaced.codec, displaced.cutoff_khz, displaced.full_band) == ("opus", 20, False), \
        "the one that lost keeps its numbers too — that is why it lost"


def test_a_replacement_records_what_the_stamps_belonged_to(tmp_path):
    """Most of this library's sidecars predate `lyrics_for_source`, so nothing could tell that
    their stamps were made against another file. A replacement is the one moment anyone knows."""
    from noaap.download import load_plan
    from noaap.lyrics import timings_stale
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")
    before = load_plan(target / plan.folder)
    before.tracks[0].lyrics = "synced"          # timed words, and nothing recorded about them
    save_plan(before, target / plan.folder)

    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE)), target)

    track = load_plan(target / plan.folder).tracks[0]
    assert track.lyrics_for_source == "Album-1", "the file that just went to the bin"
    assert track.lyrics_for_length == 200.0
    assert timings_stale(track), "and the check can say so now"


def test_words_that_were_never_timed_are_left_alone(tmp_path):
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")
    before = load_plan(target / plan.folder)
    before.tracks[0].lyrics = "plain"
    save_plan(before, target / plan.folder)

    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE)), target)

    assert load_plan(target / plan.folder).tracks[0].lyrics_for_source is None


# -- scope, and the albums this library does not have --------------------------------------------------


def test_only_one_artist_is_looked_at(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One"))
    target = library(tmp_path / "target", plan_with("Album", "One"))
    other = AlbumPlan(source_url="x://o", source_id="o", kind=Kind.OFFICIAL_ALBUM, album="Theirs",
                      albumartist="Another Band", year=None, cover_url=None, folder="Another Band/Theirs",
                      tracks=[PlanTrack(video_id="o-1", number=1, artist="Another Band", title="One",
                                        filename="01 - One.opus", provenance={})])
    save_plan(other, tmp_path / "target" / other.folder)

    mine = survey(source, target, judge=fixed(Verdict.REPLACE), artist="A Band")
    theirs = survey(source, target, judge=fixed(Verdict.REPLACE), artist="Another Band")

    assert mine.counts()["replace"] == 1
    assert theirs.counts()["replace"] == 0, "the other artist's album is not even paired"


def test_only_one_album_of_this_library_is_changed(tmp_path):
    """`--album` narrows the library being changed, not the source: an album's name is rarely the
    same on both sides — a folder called "Trust In Rust" answers to "Trust in Rust (Deluxe
    Edition)" — so filtering the incoming side by it would drop the very tracks being merged in."""
    source = library(tmp_path / "source", plan_with("Intake", "One"))
    target = library(tmp_path / "target", plan_with("Album", "One"), plan_with("Another", "One"))

    found = survey(source, target, judge=fixed(Verdict.REPLACE), album="Another")

    assert [d.name for d in found.albums()] == ["Another"]
    assert found.counts()["replace"] == 1, "the differently named source album still pairs into it"


def test_new_fetches_the_albums_this_library_lacks(tmp_path):
    from noaap.merge import take_new

    source = library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere"))
    target = library(tmp_path / "target", plan_with("Album", "One"))
    found = survey(source, target, judge=fixed(Verdict.KEEP))
    asked: list[str] = []

    done = take_new(found, lambda url: asked.append(url) or type("O", (), {"status": "ok"})())

    assert asked == ["x://Intake"], "by the ordinary path, once per album"
    assert done == {"taken": 1, "held": 0, "copied": 0, "fetched": 1}, \
        "no library given, so nothing could be copied and the fetch is the path"


def test_an_album_already_here_is_counted_as_held_not_extended(tmp_path):
    """R-164 ruling 6: a deluxe edition with extra tracks is shown, never grown into the album
    that is already here. `fetch` is what refuses; `--new` only has to not work around it."""
    from noaap.merge import take_new

    source = library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere"))
    target = library(tmp_path / "target", plan_with("Album", "One"))
    found = survey(source, target, judge=fixed(Verdict.KEEP))

    done = take_new(found, lambda url: type("O", (), {"status": "held"})())

    assert done == {"taken": 0, "held": 1, "copied": 0, "fetched": 0}


def test_the_report_names_the_albums_not_just_the_tracks(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere", "Also"))
    target = library(tmp_path / "target", plan_with("Album", "One"))

    lines = report(survey(source, target, judge=fixed(Verdict.KEEP)))

    assert any("2 track(s) in 1 album(s)" in line and "`--new` fetches those albums" in line
               for line in lines)


# -- and the ones nobody could decide (§9, slice 55) --------------------------------------------------
#
# The acceptance run of P52 found that the 288 undecided pairs left no trace in the library at all:
# the pass printed them once and the page cannot show what the plan does not hold. These cases are
# about the trace, and about the two answers that end it.


def test_an_undecided_pair_is_listed_on_the_track_and_nothing_is_copied(tmp_path):
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder",
                             body=b"other audio")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    done = carry_out(survey(source, target, judge=fixed(
        Verdict.UNDECIDED, why="the incoming file is lossless but they hold the same audio")), target)

    assert done == {"replaced": 0, "filled": 0, "offered": 1, "failed": 0}
    album_dir = target / plan.folder
    assert sorted(p.name for p in album_dir.glob("*.flac")) == [], "nothing was copied in"
    assert (album_dir / "01 - One.opus").read_bytes() == b"old audio", "and nothing was binned"
    track = load_plan(album_dir).tracks[0]
    offer = track.candidate("Intake-1")
    assert offer.undecided and offer.added_by == "pass" and offer.provider == "folder"
    assert offer.why == "the incoming file is lossless but they hold the same audio"
    assert track.chosen == "Album-1" and track.source_override is None, "listed, not chosen"
    assert [c.ref for c in track.undecided_copies()] == ["Intake-1"]


def test_both_copies_keep_their_numbers_so_a_person_can_compare_them(tmp_path):
    """The whole point of listing it: the panel has to show what the report showed."""
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    carry_out(survey(source, target, judge=fixed(Verdict.UNDECIDED)), target)

    track = load_plan(target / plan.folder).tracks[0]
    offered, here = track.candidate("Intake-1"), track.candidate("Album-1")
    assert offered.codec == "flac" and offered.cutoff_khz == 22 and offered.full_band is True
    assert here.codec == "opus" and here.cutoff_khz == 20 and here.full_band is False


def test_a_second_apply_adds_nothing(tmp_path):
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    carry_out(survey(source, target, judge=fixed(Verdict.UNDECIDED)), target)
    again = carry_out(survey(source, target, judge=fixed(Verdict.UNDECIDED)), target)

    assert again["offered"] == 0
    assert len(load_plan(target / plan.folder).tracks[0].candidates) == 2


def test_a_refused_copy_is_never_offered_again(tmp_path):
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")
    album_dir = target / plan.folder
    refused = load_plan(album_dir)
    refused.tracks[0].refuse("Intake-1")
    save_plan(refused, album_dir)

    done = carry_out(survey(source, target, judge=fixed(Verdict.UNDECIDED)), target)

    assert done["offered"] == 0
    assert load_plan(album_dir).tracks[0].candidates == refused.tracks[0].candidates


def test_a_track_the_user_worked_on_is_still_told_about_the_other_copy(tmp_path):
    """`untouchable` stops the *pass* from acting, not the user from being shown a choice: the
    verdict's reason says it is theirs, and the copy is there if they want it."""
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    carry_out(survey(source, target, judge=fixed(Verdict.UNDECIDED, why="you trimmed this one")), target)

    track = load_plan(target / plan.folder).tracks[0]
    assert track.candidate("Intake-1").why == "you trimmed this one"


def test_taking_the_offer_empties_the_list_and_refusing_it_does_too(tmp_path):
    from noaap.download import load_plan
    from noaap.merge import carry_out
    from noaap.service import apply_user_edits

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")
    carry_out(survey(source, target, judge=fixed(Verdict.UNDECIDED)), target)
    album_dir = target / plan.folder

    taken = load_plan(album_dir)
    apply_user_edits(taken, {"tracks": [{"video_id": "Album-1", "take": "Intake-1"}]})
    assert taken.tracks[0].source_override == "Intake-1" and taken.tracks[0].undecided_copies() == []
    assert taken.tracks[0].state == "pending", "the audio is fetched again, from the copy chosen"

    left = load_plan(album_dir)
    apply_user_edits(left, {"tracks": [{"video_id": "Album-1", "refuse": "Intake-1"}]})
    assert left.tracks[0].undecided_copies() == []
    assert left.tracks[0].candidate("Intake-1").undecided, "it stays listed, marked refused"


def test_only_a_copy_this_track_already_has_can_be_taken(tmp_path):
    """The user is answering the pass's question, not naming a new source — that is the field
    beside it, and parsing a ref is the provider's job, never this one's."""
    import pytest

    from noaap.service import apply_user_edits

    plan = plan_with("Album", "One")

    with pytest.raises(ValueError, match="not one of this track's known copies"):
        apply_user_edits(plan, {"tracks": [{"video_id": "Album-1", "take": "/home/someone/Music/x.flac"}]})


def test_the_report_says_the_undecided_will_be_listed(tmp_path):
    source = library(tmp_path / "source", plan_with("Intake", "One"))
    target = library(tmp_path / "target", plan_with("Album", "One"))

    lines = report(survey(source, target, judge=fixed(Verdict.UNDECIDED)))

    assert any("would be listed on their tracks" in line and "refusing is remembered" in line
               for line in lines)


def test_the_library_page_counts_the_tracks_that_wait(tmp_path):
    """Counted the way `needs_you` is, and for the same reason: nothing else lists it, and only a
    person can bring the number down."""
    from noaap.config import Config
    from noaap.download import load_plan
    from noaap.merge import carry_out
    from noaap.web import App

    source, _ = real_library(tmp_path / "source", "Intake", "One", "Two", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One", "Two")
    carry_out(survey(source, target, judge=fixed(Verdict.UNDECIDED)), target)

    rows = App(Config(musicbrainz=False, lyrics=False), target).state()["albums"]
    assert [r["copies"] for r in rows] == [2]

    left = load_plan(target / plan.folder)
    left.tracks[0].refuse("Intake-1")
    save_plan(left, target / plan.folder)
    assert [r["copies"] for r in App(Config(musicbrainz=False, lyrics=False), target).state()["albums"]] == [1]


def test_one_track_is_replaced_at_most_once_in_a_pass(tmp_path):
    """Two incoming tracks can pair with the same library track — the same song on an album and on
    a compilation, which the real material has three of. Acting twice would bin the file the run
    just wrote and leave the first bin entry naming a displacer that is gone."""
    from noaap.download import load_plan
    from noaap.merge import carry_out
    from noaap.recycle import entries as bin_entries

    source = tmp_path / "source"
    real_library(source, "Album", "One", ext="flac", provider="folder", body=b"from the album")
    real_library(source, "Compilation", "One", ext="flac", provider="folder", body=b"from the compilation")
    target, plan = real_library(tmp_path / "target", "Album", "One")

    found = survey(source, target, judge=fixed(Verdict.REPLACE, why="holds more audio"))
    done = carry_out(found, target)

    assert len(found.acting) == 2, "both copies really are proposed"
    assert done["replaced"] == 1 and done["offered"] == 1
    assert len(bin_entries(target)) == 1, "nothing was binned twice"
    track = load_plan(target / plan.folder).tracks[0]
    waiting = track.undecided_copies()
    assert len(waiting) == 1 and waiting[0].why.startswith("another copy was taken in the same pass")


def test_a_replacement_in_an_adopted_album_keeps_the_owners_name(tmp_path):
    """§9, slice 58. The album keeps its names, so the copy that replaces a file is called what the
    file was called — with the container the new file actually is, and nothing else changed."""
    from noaap.download import load_plan
    from noaap.merge import carry_out

    source, _ = real_library(tmp_path / "source", "Intake", "One", ext="flac", provider="folder")
    target, plan = real_library(tmp_path / "target", "Album", "One")
    album_dir = target / plan.folder
    theirs = load_plan(album_dir)
    theirs.keep_names = True
    theirs.tracks[0].filename = "the owner called it this.opus"
    (album_dir / "01 - One.opus").rename(album_dir / "the owner called it this.opus")
    save_plan(theirs, album_dir)

    carry_out(survey(source, target, judge=fixed(Verdict.REPLACE, why="holds more audio")), target)

    assert (album_dir / "the owner called it this.flac").is_file()
    assert load_plan(album_dir).tracks[0].filename == "the owner called it this.flac"
    assert not (album_dir / "A Band - Album - 01 - One.flac").exists(), "noaap's name stays out"


# -- and an album already on this disk is copied, not downloaded again (§9, slice 128; R-474) ----


def test_an_album_on_disk_is_copied_rather_than_fetched(tmp_path):
    """The user's own case: 211 albums, 12.5 GB, every one of them a folder on this machine.
    Fetching them would download what is already here and depend on the videos still being up."""
    from noaap.merge import take_new

    source = library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere"))
    target = library(tmp_path / "target", plan_with("Album", "One"))
    album_dir = next(p.parent for p in (tmp_path / "source").rglob(PLAN_FILE))
    (album_dir / "words.lrc").write_text("[00:00.00] la")
    found = survey(source, target, judge=fixed(Verdict.KEEP))
    asked: list[str] = []

    done = take_new(found, lambda url: asked.append(url) or type("O", (), {"status": "ok"})(),
                    library=tmp_path / "target")

    assert asked == [], "nothing was downloaded"
    assert done == {"taken": 1, "held": 0, "copied": 1, "fetched": 0}
    landed = [p for p in (tmp_path / "target").rglob("*") if p.is_file()]
    assert any(p.name == PLAN_FILE for p in landed), "the plan came with it"
    assert any(p.suffix == ".lrc" for p in landed), "and the sidecars"


def test_the_source_library_is_only_read(tmp_path):
    from noaap.merge import take_new

    source = library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere"))
    library(tmp_path / "target", plan_with("Album", "One"))
    before = {str(p.relative_to(tmp_path / "source")): p.read_bytes()
              for p in sorted((tmp_path / "source").rglob("*")) if p.is_file()}
    found = survey(source, tmp_path / "target", judge=fixed(Verdict.KEEP))

    take_new(found, lambda url: None, library=tmp_path / "target")

    assert {str(p.relative_to(tmp_path / "source")): p.read_bytes()
            for p in sorted((tmp_path / "source").rglob("*")) if p.is_file()} == before


def test_a_target_that_exists_is_never_written_into(tmp_path):
    """A deluxe edition never quietly grows the album that is here — the copy keeps that promise."""
    from noaap.download import load_plan
    from noaap.merge import copy_album

    source = library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere"))
    album_dir = next(p.parent for p in (tmp_path / "source").rglob(PLAN_FILE))
    plan = load_plan(album_dir)
    target = tmp_path / "target"
    (target / wanted_folder(plan)).mkdir(parents=True)
    (target / wanted_folder(plan) / "theirs.opus").write_bytes(b"somebody else's")

    said = []
    assert copy_album(album_dir, plan, target, log=said.append) is None
    assert [line for line in said if "already here" in line], said
    assert (target / wanted_folder(plan) / "theirs.opus").read_bytes() == b"somebody else's"


def test_a_check_says_where_it_would_go_and_copies_nothing(tmp_path):
    from noaap.download import load_plan
    from noaap.merge import copy_album

    library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere"))
    album_dir = next(p.parent for p in (tmp_path / "source").rglob(PLAN_FILE))
    plan = load_plan(album_dir)
    target = tmp_path / "target"

    said = []
    where = copy_album(album_dir, plan, target, log=said.append, apply=False)

    assert where == target / wanted_folder(plan)
    assert not where.exists()
    assert [line for line in said if "would be copied to" in line], said


def test_an_album_whose_folder_is_gone_is_still_fetched(tmp_path):
    """What `--new` meant before, and still means for an album nobody has on disk."""
    import shutil as sh

    from noaap.merge import take_new

    source = library(tmp_path / "source", plan_with("Intake", "One", "Elsewhere"))
    target = library(tmp_path / "target", plan_with("Album", "One"))
    found = survey(source, target, judge=fixed(Verdict.KEEP))
    sh.rmtree(next(p.parent for p in (tmp_path / "source").rglob(PLAN_FILE)))
    asked: list[str] = []

    done = take_new(found, lambda url: asked.append(url) or type("O", (), {"status": "ok"})(),
                    library=tmp_path / "target")

    assert asked == ["x://Intake"]
    assert done["fetched"] == 1 and done["copied"] == 0
