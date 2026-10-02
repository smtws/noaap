"""The recycle bin: noaap never removes audio, it only moves it aside (DESIGN §9, slice 49).

Deleting and pruning used to unlink. They move now, and the bin holds enough to put everything back
— the audio, the sidecar, the kept original, and the plan track exactly as it was. Nothing in here
is ever removed except by `recycle empty`, which is why every case below checks that too.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap.config import Config
from noaap.download import load_plan, run, save_plan
from noaap.lyrics import sidecar_path
from noaap.plan import build_plan
from noaap.recycle import RECYCLE, entries, total
from noaap.service import Service
from noaap.trim import ORIGINALS

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


def test_restoring_a_track_of_a_deleted_album_rebuilds_the_album_first(library):
    """Deleting an album is the largest decision a user can regret, and the source may be gone by
    then — so the plan is binned too and the folder is rebuilt from it."""
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, plan.tracks[0].video_id)
    entry = entries(tmp_path)[0]
    s.delete_album(plan.source_id)
    assert not album_dir.exists()

    assert s.restore(entry.id).status == "ok"

    back = load_plan(album_dir)
    assert back is not None, "the folder and the plan came back"
    assert [t.video_id for t in back.tracks] == [plan.tracks[0].video_id], "only the track asked for"
    assert (album_dir / back.tracks[0].filename).is_file()
    assert next(album_dir.glob("cover.*"), None), "the cover came back with the album"
    # the album entry stays: it still names the other tracks, which are still in the bin
    assert any(e.is_album for e in entries(tmp_path))


def test_restoring_the_album_entry_brings_everything_still_in_the_bin(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    (album_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff art")
    s = service(tmp_path, yt)
    s.delete_album(plan.source_id)

    album = next(e for e in entries(tmp_path) if e.is_album)
    assert s.restore(album.id).status == "ok"

    back = load_plan(album_dir)
    assert len(back.tracks) == len(plan.tracks)
    assert [t.number for t in back.tracks] == list(range(1, len(plan.tracks) + 1))
    assert (album_dir / "cover.jpg").read_bytes() == b"\xff\xd8\xff art"
    assert entries(tmp_path) == [], "everything came out of the bin"


def test_a_track_whose_album_entry_was_emptied_is_still_refused(library):
    """The one case where today's refusal is right: there is nothing left to rebuild from."""
    tmp_path, plan, yt = library
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, plan.tracks[0].video_id)
    entry = entries(tmp_path)[0]
    s.delete_album(plan.source_id)
    import shutil as sh

    for album in [e for e in entries(tmp_path) if e.is_album]:
        sh.rmtree(album.path)

    outcome = s.restore(entry.id)
    assert outcome.status == "failed"
    assert "album entry has been emptied" in outcome.message
    assert any(e.id == entry.id for e in entries(tmp_path)), "a refused restore changes nothing"


def test_restoring_into_an_album_that_was_fetched_again_merges_by_video_id(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    s = service(tmp_path, yt)
    s.delete_album(plan.source_id)

    # it comes back from the source, but with one track missing
    fresh = build_plan(vol1())
    fresh.tracks = [t for t in fresh.tracks if t.video_id != plan.tracks[0].video_id]
    run(fresh, album_dir, yt)

    album = next(e for e in entries(tmp_path) if e.is_album)
    said: list[str] = []
    s.log = said.append
    assert s.restore(album.id).status == "ok"

    back = load_plan(album_dir)
    assert {t.video_id for t in back.tracks} == {t.video_id for t in plan.tracks}, "the gap is filled"
    assert any("already there" in line for line in said), said


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


def test_deleting_an_album_bins_every_track_the_cover_and_the_plan(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    (album_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff cover")

    service(tmp_path, yt).delete_album(plan.source_id)

    found = entries(tmp_path)
    album = [e for e in found if e.is_album]
    assert len(found) == len(plan.tracks) + 1
    assert len(album) == 1
    assert album[0].cover.read_bytes() == b"\xff\xd8\xff cover"
    assert album[0].data["plan"]["source_id"] == plan.source_id
    assert sorted(album[0].data["tracks"]) == sorted(e.id for e in found if not e.is_album)
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
    from noaap.web import App

    tmp_path, plan, yt = library
    service(tmp_path, yt).delete_track(plan.source_id, plan.tracks[0].video_id)
    rows = App(Config(library_root=tmp_path), tmp_path).recycle()
    assert len(rows) == 1
    assert str(tmp_path) not in str(rows), rows
    assert set(rows[0]) == {"id", "when", "reason", "artist", "title", "album", "bytes", "track"}


# -- what the first run of P47 got wrong (R-139) ---------------------------------------------------


def test_restore_puts_the_track_back_under_its_own_number(library):
    """R-139 made the restore renumber, because deleting closed the gap behind the track and putting
    a 1 back into an album that already had a 1 gave two of them. R-373 took the closing away: the
    deletion leaves the gap, so the number the track had is the number that is free, and the album
    it comes back to is the album it left — the two rulings only hold together as a pair."""
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    s = service(tmp_path, yt)
    first = plan.tracks[0]
    s.delete_track(plan.source_id, first.video_id)
    assert [t.number for t in load_plan(album_dir).tracks] == list(range(2, len(plan.tracks) + 1)), \
        "the gap where track 1 was stays open"

    s.restore(entries(tmp_path)[0].id)

    back = load_plan(album_dir)
    numbers = [t.number for t in back.tracks]
    assert numbers == sorted(numbers), numbers
    assert len(numbers) == len(set(numbers)), f"two tracks share a number: {numbers}"
    assert numbers == list(range(1, len(plan.tracks) + 1))
    # and it went back where it stood, not onto the end
    assert back.tracks[0].video_id == first.video_id


def test_a_number_taken_while_the_track_was_gone_has_the_disc_counted_again(library):
    """The one case the old renumbering was really protecting against: something else took the
    number while the track was in the bin. Only then is the disc counted off again."""
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    s = service(tmp_path, yt)
    first = plan.tracks[0]
    s.delete_track(plan.source_id, first.video_id)
    meanwhile = load_plan(album_dir)
    meanwhile.tracks[0].number = 1          # the user dragged a row into the gap
    save_plan(meanwhile, album_dir)

    s.restore(entries(tmp_path)[0].id)

    numbers = [t.number for t in load_plan(album_dir).tracks]
    assert len(numbers) == len(set(numbers)), f"two tracks share a number: {numbers}"
    assert numbers == list(range(1, len(plan.tracks) + 1))


def test_where_the_files_state_the_numbers_nobody_elses_number_moves(tmp_path):
    """P85, from I-284. Since R-373 only the user's own reorder moves a number, so the one way the
    freed number can be taken while a track sits in the bin is that they dragged a row into the gap.
    Counting the disc off for that writes a wrong position into an adopted rip: measured on 01, 02,
    04 of 5, a track whose file says `4/5` came back as 2.
    """
    from test_folder_source import encode

    from noaap import adopt, sources
    from noaap.plan import states_numbers

    def folder():
        return sources.get("folder", Config())

    album = tmp_path / "A Band" / "A Gapped Album"
    for n, title in [(1, "One"), (2, "Two"), (4, "Four")]:
        encode(album / f"{n:02d} - {title}.mp3", title=title, artist="A Band",
               album="A Gapped Album", album_artist="A Band", track=f"{n}/5")
    adopt.carry_out(adopt.survey(tmp_path, tmp_path, folder()))
    s = Service(Config(library_root=tmp_path, musicbrainz=False, lyrics=False), tmp_path,
                log=lambda x: None)
    plan = load_plan(album)
    assert states_numbers(plan), "a folder album: the files are the authority"
    assert [(t.title, t.number) for t in plan.tracks] == [("One", 1), ("Two", 2), ("Four", 4)]

    one = next(t for t in plan.tracks if t.title == "One")
    s.delete_track(plan.source_id, one.video_id)
    meanwhile = load_plan(album)
    meanwhile.tracks[0].number = 1          # the user drags a row into the gap it left
    save_plan(meanwhile, album)

    s.restore(entries(tmp_path)[0].id)

    back = {t.title: t.number for t in load_plan(album).tracks}
    assert back["One"] == 2, "the lowest number still free on its disc"
    assert back["Two"] == 1 and back["Four"] == 4, "and nobody else's number moved"


def test_a_fetched_album_still_counts_the_disc_off_when_the_number_was_taken(library):
    """The other half: where nothing states a number, the position in the source is all one ever
    was, so the disc is counted again exactly as before."""
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    s = service(tmp_path, yt)
    first = plan.tracks[0]
    s.delete_track(plan.source_id, first.video_id)
    meanwhile = load_plan(album_dir)
    meanwhile.tracks[0].number = 1
    save_plan(meanwhile, album_dir)

    s.restore(entries(tmp_path)[0].id)

    numbers = [t.number for t in load_plan(album_dir).tracks]
    assert numbers == list(range(1, len(plan.tracks) + 1)), numbers


def test_a_binned_cover_is_called_a_cover(library):
    tmp_path, plan, yt = library
    (tmp_path / plan.folder / "cover.jpg").write_bytes(b"\xff\xd8\xff art")

    service(tmp_path, yt).delete_album(plan.source_id)

    album = next(e for e in entries(tmp_path) if e.is_album)
    assert album.cover and album.cover.name == "cover.jpg", "a cover is not audio.jpg"
    assert album.data["moved"] == ["plan", "cover"]


def test_an_entry_written_before_the_rename_still_resolves(library):
    """The first version binned a cover as its own entry called `audio.jpg`. Those entries must
    still list, because a bin that loses things is worse than no bin."""
    import json as _json

    tmp_path, plan, yt = library
    service(tmp_path, yt).delete_track(plan.source_id, plan.tracks[0].video_id)
    old = entries(tmp_path)[0].path
    data = _json.loads((old / "bin.json").read_text(encoding="utf-8"))
    data.update({"track": {}, "moved": ["cover"], "title": "(cover)"})
    (old / "bin.json").write_text(_json.dumps(data), encoding="utf-8")
    old.joinpath("audio.opus").rename(old / "audio.jpg")

    entry = entries(tmp_path)[0]
    assert entry.audio and entry.audio.name == "audio.jpg"
    assert not entry.is_album


def test_the_cli_says_why_a_restore_was_refused(library, capsys):
    """It exited 1 and printed nothing: `exit_code` returns a number and never the message."""
    from noaap.cli import main

    tmp_path, plan, yt = library
    s = service(tmp_path, yt)
    s.delete_track(plan.source_id, plan.tracks[0].video_id)
    entry = entries(tmp_path)[0]
    s.delete_album(plan.source_id)
    capsys.readouterr()

    import shutil as sh

    for album in [e for e in entries(tmp_path) if e.is_album]:
        sh.rmtree(album.path)

    assert main(["recycle", "restore", "--library", str(tmp_path), entry.id]) == 1
    said = capsys.readouterr()
    assert "album entry has been emptied" in said.err, f"stdout={said.out!r} stderr={said.err!r}"


def test_the_cli_restores_without_being_told_the_library(library, capsys, monkeypatch):
    """`--library` was the only way in, because `_recycle` read `args.library` directly instead of
    falling back to the configured root the way every other command does."""
    from noaap import config as config_mod
    from noaap.cli import main

    tmp_path, plan, yt = library
    service(tmp_path, yt).delete_track(plan.source_id, plan.tracks[0].video_id)
    entry = entries(tmp_path)[0]
    monkeypatch.setattr(config_mod, "load", lambda *a, **k: Config(library_root=tmp_path, musicbrainz=False))

    assert main(["recycle", "restore", entry.id]) == 0
    assert entries(tmp_path) == []


def test_pruning_from_the_command_line_bins_too(library, capsys, monkeypatch):
    """`_service(cfg, None)` gave prune no library, so it fell back to unlinking — the one thing
    slice 49 removed. Found while fixing the restore, not reported."""
    from noaap import config as config_mod
    from noaap.cli import main

    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    plan.tracks[0].in_source = False
    save_plan(plan, album_dir)
    monkeypatch.setattr(config_mod, "load", lambda *a, **k: Config(library_root=tmp_path, musicbrainz=False))

    assert main(["prune", "--yes", str(album_dir)]) == 0
    assert len(entries(tmp_path)) == 1
    assert entries(tmp_path)[0].reason == "no longer in the source playlist"


def test_listing_survives_being_piped_into_head(library, monkeypatch, capsys):
    """`noaap recycle list | head` closed the pipe and Python printed a traceback."""
    from noaap.cli import main

    tmp_path, plan, yt = library
    service(tmp_path, yt).delete_track(plan.source_id, plan.tracks[0].video_id)

    real = print

    def closed(*a, **k):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr("builtins.print", closed)
    try:
        assert main(["recycle", "list", "--library", str(tmp_path)]) == 0
    finally:
        monkeypatch.setattr("builtins.print", real)


def test_restore_repairs_a_track_the_plan_still_lists(library):
    """An interrupted delete — a crash, a Ctrl-C, a SIGPIPE from `delete | head` — leaves the audio
    binned and the plan still naming the track. That is deliberate: binning happens *before* the
    plan is saved, so the recoverable state is the one that survives. Restore repairs it rather
    than refusing with "already in this album"."""
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    sidecar, original = with_extras(album_dir, plan)
    track = plan.tracks[0]
    s = service(tmp_path, yt)

    # exactly what an interrupted delete_track leaves behind
    from noaap.recycle import bin_track
    from noaap.service import _inside

    bin_track(tmp_path, album_dir, plan, track, "deleted",
              audio=_inside(album_dir, track.filename), sidecar=sidecar)
    assert not (album_dir / track.filename).exists()
    assert load_plan(album_dir).tracks[0].video_id == track.video_id, "the plan never lost it"

    said: list[str] = []
    s.log = said.append
    assert s.restore(entries(tmp_path)[0].id).status == "ok"

    back = load_plan(album_dir)
    assert (album_dir / back.tracks[0].filename).is_file()
    assert sidecar_path(album_dir, back.tracks[0].filename).read_text(encoding="utf-8") == WORDS
    assert (album_dir / ORIGINALS / f"{track.video_id}.{track.ext}").is_file()
    assert len(back.tracks) == len(plan.tracks), "no duplicate was added"
    assert [t.number for t in back.tracks] == list(range(1, len(plan.tracks) + 1))
    assert any("repaired" in line for line in said), said
    assert entries(tmp_path) == []


def test_a_track_that_is_really_there_is_still_refused(library):
    """The repair must not become "restore over whatever is on disk"."""
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    track = plan.tracks[0]
    s = service(tmp_path, yt)

    from noaap.recycle import bin_track
    from noaap.service import _inside

    bin_track(tmp_path, album_dir, plan, track, "deleted", audio=_inside(album_dir, track.filename))
    (album_dir / track.filename).write_bytes(b"a different file is here now")

    outcome = s.restore(entries(tmp_path)[0].id)
    assert outcome.status == "failed"
    assert "already in" in outcome.message
    assert (album_dir / track.filename).read_bytes() == b"a different file is here now"
