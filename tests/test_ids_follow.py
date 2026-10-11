"""A folder track's id follows its file (DESIGN §9, slice 180; P128).

The update of 2026-10-11 moved 18 albums' tracks into the seats MusicBrainz gives them and left each
renamed track's `video_id` on the name it had before: `./…17 - Nachtschleife 'Nach Sendeschluss'.mp3`
for a file that is now `…14 - …`. A read of the library afterwards found 3561 ids in 602 albums naming
a file that is not there — every one of them a track whose audio a re-fetch, a source switch or an
audio choice would have looked for under that name and failed to find.
"""

from __future__ import annotations

from pathlib import Path

from test_intake import QUIET
from test_repointing import a_library, an_album

from noaap import intake, trim
from noaap.download import load_plan, point_at, save_plan, tracks_elsewhere
from noaap.lyrics import timings_stale


def taken_in(root, tone, titles=("One", "Two", "Three")):
    album = an_album(root / "A Band" / "An Album", list(titles), tone)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    plan = load_plan(album)
    for track in plan.tracks:
        assert track.video_id == str(album / track.filename), "a take-in records the file itself"
    return album


def gone_ids(plan, album):
    return [t.video_id for t in plan.tracks
            if str(t.video_id).startswith(str(album)) and not Path(t.video_id).exists()]


def runs(root, album):
    service = a_library(root, retag_adopted=True, rename_adopted=True)
    lines: list[str] = []
    service.log = lines.append
    service.download_existing(album)
    return lines


# -- 1. a rename inside the pass takes the id with it ----------------------------------------------


def test_a_seat_shift_takes_every_id_with_its_file(tmp_path, one_second_of_sound):
    """Three tracks move one seat on: 1 → 2, 2 → 3, 3 → 1 — a cycle, renamed through temporary names."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    before = {t.title: t.filename for t in plan.tracks}
    for track in plan.tracks:
        track.number = track.number % 3 + 1
    save_plan(plan, album)

    runs(root, album)

    after = load_plan(album)
    assert {t.title: t.filename for t in after.tracks} != before, "the files were renamed"
    for track in after.tracks:
        assert track.video_id == str(album / track.filename), (track.title, track.video_id)
        assert [c.ref for c in track.candidates] == [track.video_id], track.candidates
    assert gone_ids(after, album) == []
    assert point_at(after, album) is False, "nothing left for a repair to do"


def test_the_source_state_and_the_originals_follow_too(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    one = next(t for t in plan.tracks if t.title == "One")
    kept = trim.original_path(album, one)
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(b"the untouched one")
    plan.source_state = {"ids": [t.video_id for t in plan.tracks]}
    for track in plan.tracks:
        track.number = track.number % 3 + 1
    save_plan(plan, album)

    runs(root, album)

    after = load_plan(album)
    one = next(t for t in after.tracks if t.title == "One")
    now = trim.original_path(album, one)
    assert now != kept and not kept.exists(), "kept under the name the file has now"
    assert now.read_bytes() == b"the untouched one"
    assert sorted(after.source_state["ids"]) == sorted(str(album / t.filename) for t in after.tracks)


def test_an_update_leaves_no_id_on_a_name_it_has_just_changed(tmp_path, one_second_of_sound):
    """Through `update`, the pass that did it on the share: a re-tagged title renames the file."""
    from mutagen import File as MFile

    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    two = next(t for t in plan.tracks if t.title == "Two")
    audio = MFile(album / two.filename)
    audio["title"] = ["Zwei"]
    audio.save()

    service = a_library(root, retag_adopted=True, rename_adopted=True)
    service.update_all(deep=True)

    after = load_plan(album)
    zwei = next(t for t in after.tracks if t.title == "Zwei")
    assert "Zwei" in zwei.filename, zwei.filename
    for track in after.tracks:
        assert track.video_id == str(album / track.filename), (track.title, track.video_id)
    assert gone_ids(after, album) == []


# -- 2. the words' source goes with the id -----------------------------------------------------------


def test_a_re_pointed_timed_track_is_not_called_timed_for_another_file(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    one = plan.tracks[0]
    stale = str(album / "01 an old name.opus")
    one.video_id = stale
    one.sync_candidates()
    one.lyrics, one.lyrics_for_source = "synced", stale
    one.trimmed = True
    assert timings_stale(one) is None, "stale, but consistent with itself"

    assert point_at(plan, album) is True
    assert one.video_id == str(album / one.filename)
    assert one.lyrics_for_source == one.video_id
    assert timings_stale(one) is None


def test_a_seat_shift_carries_the_words_source(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    for track in plan.tracks:
        track.lyrics, track.lyrics_for_source = "synced", track.video_id
        track.number = track.number % 3 + 1
    save_plan(plan, album)

    runs(root, album)

    for track in load_plan(album).tracks:
        assert track.lyrics_for_source == track.video_id == str(album / track.filename)
        assert timings_stale(track) is None


# -- 3. an id on a neighbour's file is stale, not the neighbour's ------------------------------------


def test_an_id_naming_another_tracks_file_is_re_pointed_at_its_own(tmp_path, one_second_of_sound):
    """The state a shift leaves when ids stay behind: 1's id names 2's file, 2's names 3's."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    files = [t.filename for t in plan.tracks]
    for track, theirs in zip(plan.tracks, [*files[1:], "03 gone.opus"], strict=True):
        track.video_id = str(album / theirs)
        track.sync_candidates()

    assert len(tracks_elsewhere(plan, album)) == 3
    assert point_at(plan, album) is True
    for track in plan.tracks:
        assert track.video_id == str(album / track.filename), (track.title, track.video_id)


def test_a_fetch_never_copies_a_neighbours_file(tmp_path, one_second_of_sound):
    """A track to be fetched again whose id names the next track's file: the copy would be that
    track's audio under this one's name."""
    from noaap.download import run
    from noaap.sources_folder import FolderSource

    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    one, two = plan.tracks[0], plan.tracks[1]
    (album / one.filename).unlink()
    one.video_id = str(album / two.filename)
    one.sync_candidates()
    one.state = "pending"

    run(plan, album, FolderSource(), download=True, lyrics=None)

    assert not (album / one.filename).exists(), "nothing was copied in under 01's name"
    assert one.state == "failed"
    assert two.filename in (one.error or "") or "another track" in (one.error or ""), one.error


# -- 4. one id, one track ---------------------------------------------------------------------------


def test_point_at_never_gives_two_tracks_one_id(tmp_path, one_second_of_sound):
    """1's file is gone and its own name is what 2's (stale) id still says: re-pointing 1 there would
    give both tracks one id, and every lookup by id would answer with whichever comes first."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    one, two = plan.tracks[0], plan.tracks[1]
    two.video_id = str(album / one.filename)
    two.filename = "02 somewhere else.opus"     # 2's file is not there, so 2 is not re-pointed
    two.sync_candidates()
    one.video_id = str(album / "01 an old name.opus")
    one.sync_candidates()
    was = plan.to_dict()

    said: list[str] = []
    assert point_at(plan, album, say=said.append) is False
    assert plan.to_dict() == was, "the album's plan is left as it was"
    assert len(said) == 1, said
    assert one.title in said[0] and two.title in said[0], said[0]


# -- 5. the name a person sees is the name the file has ------------------------------------------------


def test_delete_finds_a_track_by_the_file_it_has_now_and_by_its_id(tmp_path, one_second_of_sound):
    from noaap.download import track_named

    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    one, two = plan.tracks[0], plan.tracks[1]
    one.video_id = str(album / "01 an old name.opus")
    one.sync_candidates()

    assert track_named(plan, album, "./01 an old name.opus") is one, "the id the plan file shows"
    assert track_named(plan, album, one.filename) is one, "the file's own name"
    assert track_named(plan, album, f"./{one.filename}") is one
    assert track_named(plan, album, str(album / two.filename)) is two
    assert track_named(plan, album, "./nothing.opus") is None


def test_the_cli_deletes_a_track_named_by_its_current_file(tmp_path, one_second_of_sound, monkeypatch):
    from noaap.cli import main

    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    plan = load_plan(album)
    one = plan.tracks[0]
    one.video_id = str(album / "01 an old name.opus")
    one.sync_candidates()
    save_plan(plan, album)

    conf = tmp_path / "xdg" / "noaap"
    conf.mkdir(parents=True)
    (conf / "config.toml").write_text(f'library_root = "{root}"\nlyrics = false\nmusicbrainz = false\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert main(["delete", str(album), "--track", one.filename, "--yes"]) == 0
    assert one.title not in [t.title for t in load_plan(album).tracks]
