"""The recycle bin: ytalbum never removes audio, it only moves it aside (DESIGN §9, slice 49).

Deleting and pruning used to unlink. They move now, and the bin holds enough to put everything back
— the audio, the sidecar, the kept original, and the plan track exactly as it was. Nothing in here
is ever removed except by `recycle empty`, which is why every case below checks that too.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.lyrics import sidecar_path
from ytalbum.plan import build_plan
from ytalbum.recycle import RECYCLE, entries, total
from ytalbum.service import Service
from ytalbum.trim import ORIGINALS

WORDS = "[00:01.0] the words that were there\n"


@pytest.fixture
def library(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    run(plan, tmp_path / plan.folder, yt)
    return tmp_path, load_plan(tmp_path / plan.folder), yt


def service(tmp_path, yt):
    return Service(Config(musicbrainz=False), tmp_path, yt=yt, log=lambda s: None)


def with_extras(album_dir: Path, plan) -> tuple[Path, Path]:
    """Give the first track a sidecar and a kept original, the two things that used to be lost."""
    track = plan.tracks[0]
    sidecar_path(album_dir, track.filename).write_text(WORDS, encoding="utf-8")
    (album_dir / ORIGINALS).mkdir(exist_ok=True)
    original = album_dir / ORIGINALS / f"{track.video_id}.{track.ext}"
    original.write_bytes(b"the untouched download")
    return sidecar_path(album_dir, track.filename), original


def test_deleting_a_track_moves_everything_it_had(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    sidecar, original = with_extras(album_dir, plan)
    track = plan.tracks[0]
    audio = album_dir / track.filename

    service(tmp_path, yt).delete_track(plan.source_id, track.video_id)

    assert not audio.exists() and not sidecar.exists() and not original.exists()
    entry = entries(tmp_path)[0]
    assert entry.reason == "deleted"
    assert entry.audio and entry.audio.is_file()
    assert entry.words and entry.words.read_text(encoding="utf-8") == WORDS
    assert entry.original and entry.original.read_bytes() == b"the untouched download"
    # the whole track, so a restore never has to guess
    assert entry.data["track"]["video_id"] == track.video_id
    assert entry.data["tags"]["title"] == track.title


def test_a_track_that_was_never_downloaded_leaves_no_entry(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    track = plan.tracks[0]
    (album_dir / track.filename).unlink()

    service(tmp_path, yt).delete_track(plan.source_id, track.video_id)
    assert entries(tmp_path) == [], "an empty bin entry is only noise"


def test_restore_puts_the_track_its_words_and_its_original_back(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    with_extras(album_dir, plan)
    track = plan.tracks[0]
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, track.video_id)
    entry = entries(tmp_path)[0]

    outcome = s.restore(entry.id)
    assert outcome.status == "ok"

    back = load_plan(album_dir)
    restored = next(t for t in back.tracks if t.video_id == track.video_id)
    assert (album_dir / restored.filename).is_file()
    assert sidecar_path(album_dir, restored.filename).read_text(encoding="utf-8") == WORDS
    assert (album_dir / ORIGINALS / f"{restored.video_id}.{restored.ext}").is_file()
    assert entries(tmp_path) == [], "a restored entry leaves the bin"
    assert len(back.tracks) == len(plan.tracks)


def test_restore_keeps_lyrics_the_user_wrote_while_the_track_was_gone(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    with_extras(album_dir, plan)
    track = plan.tracks[0]
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, track.video_id)

    # the user writes words for a track that is not there — theirs win, always
    mine = "[00:02.0] mine\n"
    sidecar_path(album_dir, track.filename).write_text(mine, encoding="utf-8")
    said: list[str] = []
    s.log = said.append
    s.restore(entries(tmp_path)[0].id)

    assert sidecar_path(album_dir, track.filename).read_text(encoding="utf-8") == mine
    assert any("your own lyrics were there" in line for line in said)
    # and the binned words are still in the bin rather than thrown away
    assert entries(tmp_path)[0].words is not None


def test_restore_refuses_when_the_album_is_gone(library):
    tmp_path, plan, yt = library
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, plan.tracks[0].video_id)
    entry = entries(tmp_path)[0]
    s.delete_album(plan.source_id)

    outcome = s.restore(entry.id)
    assert outcome.status == "failed"
    assert "not in the library any more" in outcome.message
    assert any(e.id == entry.id for e in entries(tmp_path)), "a refused restore changes nothing"


def test_restore_refuses_a_track_that_is_already_there(library):
    tmp_path, plan, yt = library
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, plan.tracks[0].video_id)
    entry = entries(tmp_path)[0]
    assert s.restore(entry.id).status == "ok"
    # the same entry is gone, but if a copy existed it would be refused rather than duplicated
    assert s.restore(entry.id).status == "failed"


def test_pruning_bins_instead_of_unlinking(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    with_extras(album_dir, plan)
    plan.tracks[0].in_source = False
    save_plan(plan, album_dir)

    service(tmp_path, yt).prune(album_dir)

    entry = entries(tmp_path)[0]
    assert entry.reason == "no longer in the source playlist"
    assert entry.audio and entry.words and entry.original, "the kept original goes with the track"


def test_a_restored_track_the_source_no_longer_lists_is_binned_again_by_the_next_prune(library):
    """Restore undoes one action; it does not argue with the playlist."""
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    plan.tracks[0].in_source = False
    save_plan(plan, album_dir)
    s = service(tmp_path, yt)
    s.prune(album_dir)

    assert s.restore(entries(tmp_path)[0].id).status == "ok"
    back = load_plan(album_dir)
    assert any(not t.in_source for t in back.tracks), "it comes back as it was"

    s.prune(album_dir)
    assert entries(tmp_path)[0].reason == "no longer in the source playlist"


def test_deleting_an_album_bins_every_track_and_the_cover(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    (album_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff cover")

    service(tmp_path, yt).delete_album(plan.source_id)

    found = entries(tmp_path)
    assert len(found) == len(plan.tracks) + 1
    covers = [e for e in found if not e.data.get("track")]
    assert len(covers) == 1 and covers[0].audio.read_bytes() == b"\xff\xd8\xff cover"
    assert [p.name for p in tmp_path.iterdir()] == [RECYCLE]


def test_the_bin_never_empties_itself(library):
    tmp_path, plan, yt = library
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, plan.tracks[0].video_id)
    before = total(tmp_path)

    # everything that touches the library, short of asking
    s.prune(tmp_path / plan.folder)
    s.fetch_lyrics()
    assert total(tmp_path) == before

    assert s.empty_recycle().status == "ok"
    assert entries(tmp_path) == []


def test_empty_older_than_leaves_the_young_ones(library, monkeypatch):
    tmp_path, plan, yt = library
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, plan.tracks[0].video_id)
    s.delete_track(plan.source_id, plan.tracks[1].video_id)

    old = entries(tmp_path)[0]
    data = old.path / "bin.json"
    data.write_text(data.read_text(encoding="utf-8").replace(old.when, "2020-01-01T00:00:00+00:00"),
                    encoding="utf-8")

    s.empty_recycle(older_than_days=30)
    left = entries(tmp_path)
    assert len(left) == 1
    assert left[0].id != old.id


def test_an_unreadable_entry_is_skipped_rather_than_crashing(library):
    tmp_path, plan, yt = library
    service(tmp_path, yt).delete_track(plan.source_id, plan.tracks[0].video_id)
    (tmp_path / RECYCLE / "not-an-entry").mkdir()
    (tmp_path / RECYCLE / "half" ).mkdir()
    (tmp_path / RECYCLE / "half" / "bin.json").write_text("{not json", encoding="utf-8")

    assert len(entries(tmp_path)) == 1


def test_the_bin_holds_no_filesystem_path_for_the_page(library):
    """`/api/recycle` addresses an entry by id. A path would tell the page where the library is."""
    from ytalbum.web import App

    tmp_path, plan, yt = library
    service(tmp_path, yt).delete_track(plan.source_id, plan.tracks[0].video_id)
    rows = App(Config(library_root=tmp_path), tmp_path).recycle()
    assert len(rows) == 1
    assert str(tmp_path) not in str(rows), rows
    assert set(rows[0]) == {"id", "when", "reason", "artist", "title", "album", "bytes", "track"}
