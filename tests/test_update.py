"""The cheap update: skip unchanged albums, but never miss a real change."""

import copy
import json
from pathlib import Path

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap.config import Config
from noaap.download import load_plan, run, save_plan
from noaap.models import Provenance
from noaap.plan import build_plan, merge_plans
from noaap.service import Service, apply_user_edits


class CountingYouTube(FakeYouTube):
    """Answers the cheap check from a list of ids; counts what was asked."""

    def __init__(self, template, ids, modified="20260101"):
        super().__init__(template)
        self.ids, self.modified = ids, modified
        self.state_calls, self.full_fetches = 0, 0

    def changed(self, url):
        self.state_calls += 1
        return {"ids": list(self.ids), "modified": self.modified}

    def collection(self, url):
        self.full_fetches += 1
        collection = vol1()
        collection.entries = [e for e in collection.entries if e.video_id in self.ids]
        collection.modified = self.modified
        return collection


@pytest.fixture
def library(tmp_path, opus_template):
    collection = vol1()
    collection.modified = "20260101"  # as a real read records it
    plan = build_plan(collection)
    ids = [e.video_id for e in collection.entries]
    yt = CountingYouTube(opus_template, ids)
    run(plan, tmp_path / plan.folder, yt)
    save_plan(plan, tmp_path / plan.folder)
    return tmp_path, plan, yt


def service(tmp_path, yt):
    return Service(Config(musicbrainz=False), tmp_path, yt=yt)


def test_unchanged_album_costs_one_request(library):
    tmp_path, plan, yt = library
    outcomes = service(tmp_path, yt).update_all()
    assert [o.message for o in outcomes] == ["unchanged"]
    assert (yt.state_calls, yt.full_fetches) == (1, 0)


def test_a_changed_playlist_is_read_in_full(library):
    tmp_path, plan, yt = library
    yt.ids = yt.ids[:-1]  # a video left the playlist
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_a_new_modification_date_is_read_in_full(library):
    tmp_path, plan, yt = library
    yt.modified = "20260202"
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_an_incomplete_album_is_always_read(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    saved = load_plan(album_dir)
    saved.tracks[0].state = "failed"
    save_plan(saved, album_dir)
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_a_track_waiting_for_a_decision_does_not_force_a_full_read(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    saved = load_plan(album_dir)
    saved.tracks[0].state, saved.tracks[0].error_kind = "failed", "no_audio_stream"
    save_plan(saved, album_dir)
    outcomes = service(tmp_path, yt).update_all()
    assert yt.full_fetches == 0 and outcomes[0].message == "unchanged"


def test_deep_reads_everything(library):
    tmp_path, plan, yt = library
    service(tmp_path, yt).update_all(deep=True)
    assert (yt.state_calls, yt.full_fetches) == (0, 1)


def test_an_album_from_before_this_existed_is_read_once(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    saved = load_plan(album_dir)
    saved.source_state = {}  # an album from before this existed
    save_plan(saved, album_dir)
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_a_failing_quick_check_falls_back_to_reading(library):
    tmp_path, plan, yt = library

    def boom(url):
        raise RuntimeError("network hiccup")

    yt.changed = boom
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_update_can_be_limited_to_one_artist(tmp_path, opus_template):
    from noaap.models import Collection

    first = vol1()
    plan_one = build_plan(first)
    yt = CountingYouTube(opus_template, [e.video_id for e in first.entries])
    run(plan_one, tmp_path / plan_one.folder, yt)

    other = vol1()  # a second album under a different artist
    other.source_id, other.source_url, other.channel = "PLother", "https://www.youtube.com/playlist?list=PLother", "Someone Else"
    plan_two = build_plan(other)
    plan_two.albumartist = "Someone Else"
    from noaap.plan import refresh_derived

    refresh_derived(plan_two)
    run(plan_two, tmp_path / plan_two.folder, yt)

    yt.state_calls = 0
    Service(Config(musicbrainz=False), tmp_path, yt=yt).update_all(artist="Someone Else")
    assert yt.state_calls == 1  # only that artist's album was checked


# -- losing access to a source must not cost the files already downloaded -------------------

PREMIUM = "This video is only available to Music Premium members"


class LostAccess:
    """The playlist still lists everything, but every video has become unreadable."""

    def __init__(self, collection):
        self.whole = collection

    def changed(self, url):
        return {"ids": [e.video_id for e in self.whole.entries], "modified": None}

    def collection(self, url):
        gone = copy.deepcopy(self.whole)
        for e in gone.entries:
            e.skipped, e.transient = PREMIUM, False  # permanent, like a cancelled subscription
        return gone


def test_a_cancelled_subscription_does_not_empty_the_album(tmp_path, opus_template):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, FakeYouTube(opus_template))
    save_plan(plan, album_dir)
    before = sorted(p.name for p in album_dir.glob("*.opus"))
    assert before

    service = Service(Config(library_root=tmp_path), tmp_path, yt=LostAccess(vol1()), mb=None)
    outcomes = service.update_all(deep=True)  # deep: force the full read, not the cheap check

    assert [o.status for o in outcomes] == ["failed"]
    assert "nothing to download" in outcomes[0].message
    after = load_plan(album_dir)
    assert sorted(p.name for p in album_dir.glob("*.opus")) == before  # every file still there
    assert [t.title for t in after.tracks] == [t.title for t in plan.tracks]
    assert all(t.state == "done" and t.in_source for t in after.tracks)


def test_an_unreadable_video_still_counts_as_being_in_the_source(tmp_path, opus_template):
    """Otherwise the UI would offer to prune tracks that never left the playlist."""
    plan = build_plan(vol1())
    fresh = build_plan(vol1())
    fresh.tracks = fresh.tracks[:2]
    fresh.skipped = [{"video_id": t.video_id, "title": t.title, "reason": PREMIUM} for t in plan.tracks[2:]]
    merged = merge_plans(plan, fresh)
    assert all(t.in_source for t in merged.tracks)


# -- an order the user set belongs to them, not to the playlist -----------------------------


def user_ordered(plan, sequence):
    """Apply a new order the way the UI does: numbers in, renumbered and claimed."""
    by_id = {t.video_id: t for t in plan.tracks}
    edits = {"tracks": [{"video_id": vid, "number": n} for n, vid in enumerate(sequence, 1)]}
    return apply_user_edits(plan, edits), by_id


def test_a_user_order_is_kept_when_the_playlist_is_read_again():
    plan = build_plan(vol1())
    reversed_ids = [t.video_id for t in reversed(plan.tracks)]
    apply_user_edits(plan, {"tracks": [{"video_id": v, "number": n} for n, v in enumerate(reversed_ids, 1)]})
    assert plan.provenance["order"] == Provenance.USER
    assert [t.video_id for t in plan.tracks] == reversed_ids

    merged = merge_plans(plan, build_plan(vol1()))  # the source still lists its own order
    assert [t.video_id for t in merged.tracks] == reversed_ids
    assert [t.number for t in merged.tracks] == list(range(1, len(reversed_ids) + 1))


def test_a_video_that_appears_later_goes_to_the_end_of_a_user_order():
    plan = build_plan(vol1())
    ids = [t.video_id for t in plan.tracks]
    apply_user_edits(plan, {"tracks": [{"video_id": v, "number": n} for n, v in enumerate(reversed(ids), 1)]})
    short = build_plan(vol1())
    short.tracks = [t for t in short.tracks if t.video_id != ids[0]]
    plan.tracks = [t for t in plan.tracks if t.video_id != ids[0]]  # we never had it

    merged = merge_plans(plan, build_plan(vol1()))
    late = next(t for t in merged.tracks if t.video_id == ids[0])
    assert late.number == len(merged.tracks)  # last, not wherever the playlist puts it


def test_without_a_user_order_the_source_still_decides():
    plan = build_plan(vol1())
    plan.tracks = list(reversed(plan.tracks))  # reordered by something other than the user
    merged = merge_plans(plan, build_plan(vol1()))
    assert [t.video_id for t in merged.tracks] == [t.video_id for t in build_plan(vol1()).tracks]


# -- a file the plan already holds is that plan's track (§9, slice 135; R-488 / P100) ------------


def superseded(tmp_path, one_second_of_sound, how_many=3, overridden=2):
    """A folder album in the state 20 of the user's albums are in: some tracks' owner files were
    superseded by a fetched copy, so the plan's id names a file that is gone while the file in the
    folder is the replacement."""
    from test_intake import QUIET
    from test_repointing import a_library, an_album

    from noaap import intake

    root = tmp_path / "collection"
    album = an_album(root / "A Band" / "An Album",
                     [f"Song {n}" for n in range(1, how_many + 1)], one_second_of_sound)
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    album = root / "A Band" / "An Album"
    plan = load_plan(album)
    for track in plan.tracks[:overridden]:
        # the owner's copy was an mp3, a pass took a video instead, and the mp3 is gone
        track.video_id = str(album / (Path(track.filename).stem + ".mp3"))
        track.source_override = f"vid{track.number}"
        track.sync_candidates()
    save_plan(plan, album)
    return root, album


def test_a_superseded_track_is_the_same_track_not_a_new_one(tmp_path, one_second_of_sound):
    """`The Dead Don't Die`: 20 tracks, 17 of them overridden. Matching on the id alone made the
    update report all 17 gone *and* their own files new — 37 tracks, numbers 1,1,2,2,…,28, and 37
    audio files in the folder. Measured on a copy before any of it reached the library."""
    from test_repointing import a_library

    root, album = superseded(tmp_path, one_second_of_sound, how_many=3, overridden=2)
    was = load_plan(album)

    said = []
    service = a_library(root)
    service.log = said.append
    service.update_all(deep=True)   # the album is otherwise skipped as unchanged, and nothing merges

    now = load_plan(album)
    assert len(now.tracks) == len(was.tracks) == 3, [t.title for t in now.tracks]
    assert sorted(t.number for t in now.tracks) == [1, 2, 3]
    assert all(t.in_source for t in now.tracks), [(t.title, t.in_source) for t in now.tracks]
    assert [t.video_id for t in now.tracks] == [t.video_id for t in was.tracks], "the ids are kept"
    files = sorted(p.name for p in album.iterdir() if p.suffix.lower() == ".opus")
    assert len(files) == 3, files
    assert not [line for line in said if "no longer in the source" in line and "0 no longer" not in line], said


def test_two_tracks_that_could_answer_leave_it_to_the_id(tmp_path, one_second_of_sound):
    """Never a guess: where the same relative name is held by two tracks, the match is the id's, as
    it was before."""
    from noaap.plan import as_the_plan_knows_them

    root, album = superseded(tmp_path, one_second_of_sound, how_many=2, overridden=1)
    existing = load_plan(album)
    existing.tracks[1].filename = existing.tracks[0].filename   # two tracks, one name
    fresh = copy.deepcopy(existing)
    fresh.tracks[0].video_id = str(album / existing.tracks[0].filename)

    out = as_the_plan_knows_them(existing, fresh)

    assert out.tracks[0].video_id == str(album / existing.tracks[0].filename), "left to the id"


def test_a_track_in_a_disc_folder_is_only_that_disc_folders_track(tmp_path, one_second_of_sound):
    """Matched on the path relative to the folder that was read, so `cd1/01.opus` is never
    `cd2/01.opus`."""
    from noaap.plan import as_the_plan_knows_them

    root, album = superseded(tmp_path, one_second_of_sound, how_many=2, overridden=0)
    existing = load_plan(album)
    existing.tracks[0].filename = "cd1/01 Song.opus"
    existing.tracks[1].filename = "cd2/01 Song.opus"
    existing.tracks[0].video_id = "/gone/cd1/01 Song.opus"
    existing.tracks[1].video_id = "/gone/cd2/01 Song.opus"
    fresh = copy.deepcopy(existing)
    fresh.tracks[0].video_id = str(album / "cd1/01 Song.opus")
    fresh.tracks[1].video_id = str(album / "cd2/01 Song.opus")

    out = as_the_plan_knows_them(existing, fresh)

    assert out.tracks[0].video_id == "/gone/cd1/01 Song.opus"
    assert out.tracks[1].video_id == "/gone/cd2/01 Song.opus"


def test_a_fetched_album_is_not_matched_by_file_at_all(tmp_path, one_second_of_sound):
    """Only a folder's refs are paths. A YouTube album's ids are video ids and stay the only answer."""
    from noaap.plan import as_the_plan_knows_them

    root, album = superseded(tmp_path, one_second_of_sound, how_many=2, overridden=0)
    existing = load_plan(album)
    existing.provider = "youtube"
    fresh = copy.deepcopy(existing)
    fresh.provider = "youtube"
    fresh.tracks[0].video_id = "abcdefghijk"

    out = as_the_plan_knows_them(existing, fresh)

    assert out.tracks[0].video_id == "abcdefghijk"
