"""Taking a whole collection in, so that all of it ends in one state (DESIGN §9, slice 101).

The user's collection is twenty years old, eleven thousand tracks, and has no backup. These cases are
about the pass that walks it: that a dry run writes nothing at all, that a real run leaves every
album adopted and treated, that it can be interrupted and resumed without doing an album twice, and
that what it wrote can be put back.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from mutagen import File as MFile

from noaap import intake, precautions
from noaap.config import Config
from noaap.download import load_plan
from noaap.service import Service
from noaap.tag import embedded_cover


@pytest.fixture
def collection(tmp_path, one_second_of_sound):
    """Somebody's own folder: two artists, three albums, their names, their tags, no plan anywhere."""
    root = tmp_path / "collection"
    for album, titles in (("Aphelion/Nocturnes", ["First", "Second"]),
                          ("Aphelion/Vigil", ["Only"]),
                          ("Bramblewood/Hollow", ["One", "Two", "Three"])):
        folder = root / album
        folder.mkdir(parents=True)
        for n, title in enumerate(titles, 1):
            path = folder / f"{n:02d} {title}.opus"
            shutil.copy(one_second_of_sound, path)
            audio = MFile(path)
            audio["title"] = [title]
            audio["artist"] = [album.split("/")[0]]
            audio["album"] = [album.split("/")[1]]
            audio["comment"] = ["ripped by me in 2006"]
            audio.save()
    return root


@pytest.fixture
def service(tmp_path):
    """A service whose library is the collection itself, asking nobody anything."""
    library = tmp_path / "collection"
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    return Service(cfg, library, log=lambda s: None)


QUIET = intake.Choices(musicbrainz=False, lyrics=False)


def test_a_dry_run_says_what_it_would_do_and_writes_nothing(collection, service, tmp_path):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in sorted(collection.rglob("*")) if p.is_file()}
    said = []

    done = intake.take_in(service, collection, QUIET, dry_run=True, log=said.append)

    assert done.albums == 3 and done.adopted == 3 and done.tracks == 6
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(collection.rglob("*")) if p.is_file()} == before
    assert not list(tmp_path.glob("*.jsonl")), "not even the snapshot: a dry run writes nothing"
    assert any("would be renamed" in line for line in done.would), done.would
    assert any("nothing was written" in line for line in said)


def test_a_real_run_adopts_and_treats_every_album(collection, service, tmp_path):
    snapshot = tmp_path / "snap.jsonl"
    done = intake.take_in(service, collection, QUIET, dry_run=False, snapshot=snapshot,
                          log=lambda s: None)

    assert done.adopted == 3 and done.tracks == 6
    assert snapshot.is_file(), "written before the first write to a file"
    for album in ("Aphelion/Nocturnes", "Aphelion/Vigil", "Bramblewood/Hollow"):
        plan = load_plan(collection / album)
        assert plan is not None and plan.adopted, f"{album} is adopted"
        for track in plan.tracks:
            path = collection / album / track.filename
            assert path.is_file()
            assert MFile(path)["comment"] == ["ripped by me in 2006"], "their own field is kept"
            assert MFile(path)["album"] == [album.split("/")[1]]


def test_the_names_are_the_collections_own_when_asked(collection, service, tmp_path):
    keep = intake.Choices(names="keep", musicbrainz=False, lyrics=False)
    intake.take_in(service, collection, keep, dry_run=False, snapshot=tmp_path / "s.jsonl",
                   log=lambda s: None)
    assert (collection / "Aphelion/Nocturnes/01 First.opus").is_file()


def test_a_run_that_stops_is_resumed_where_it_stood(collection, service, tmp_path):
    snapshot = tmp_path / "snap.jsonl"
    state = intake.state_path(snapshot)

    class Stops(Exception):
        pass

    def check():
        # the machine stops once two albums have been written down as done
        if len(intake.read_state(state)) >= 2:
            raise Stops()
    service.check = check

    with pytest.raises(Stops):
        intake.take_in(service, collection, QUIET, dry_run=False, snapshot=snapshot, log=lambda s: None)
    done_first = intake.read_state(state)
    assert len(done_first) == 2, "what it finished is written down, album by album"

    service.check = lambda: None
    said = []
    done = intake.take_in(service, collection, QUIET, dry_run=False, snapshot=snapshot, log=said.append)

    assert any("were done by an earlier run" in line for line in said)
    assert done.adopted == 1, "only the album that was left"
    assert len(intake.read_state(state)) == 3


def test_everything_it_wrote_can_be_put_back(collection, service, tmp_path):
    snapshot = tmp_path / "snap.jsonl"
    keep = tmp_path / "originals"
    before = {str(p.relative_to(collection)): p.read_bytes()
              for p in precautions.audio_under(collection)}

    intake.take_in(service, collection, QUIET, dry_run=False, snapshot=snapshot, keep=keep,
                   log=lambda s: None)
    after = {str(p.relative_to(collection)): p.read_bytes()
             for p in precautions.audio_under(collection)}
    assert after != before, "the pass did something"

    snap = precautions.read(snapshot)
    put_back = precautions.restore(snap, collection, apply=True, kept=keep, log=lambda s: None)

    assert put_back.missing == [] and put_back.changed == [] and put_back.lost == []
    assert {str(p.relative_to(collection)): p.read_bytes()
            for p in precautions.audio_under(collection)} == before, "byte for byte"


def test_what_is_not_an_album_is_refused_and_named(collection, service, tmp_path):
    (collection / "Aphelion" / "notes.txt").write_text("not music")
    mixed = collection / "Mixed"
    mixed.mkdir()
    for name, album, seconds in (("a.opus", "One", 1), ("b.opus", "Another", 2)):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=duration={seconds}",
                        "-c:a", "libopus", str(mixed / name)], check=True)
        audio = MFile(mixed / name)
        audio["album"] = [album]
        audio["artist"] = ["Somebody"]
        audio["title"] = [name]
        audio.save()

    done = intake.take_in(service, collection, QUIET, dry_run=True, log=lambda s: None)
    assert "Mixed" in done.refused, done.refused
    assert done.adopted == 3, "and the three real albums are unaffected"


def test_the_choices_are_a_treatment_and_are_not_recorded(collection, service, tmp_path):
    quick = intake.Choices(names="keep", musicbrainz=False, lyrics=False, cover_embedded=False,
                           lyrics_embedded=False)
    intake.take_in(service, collection, quick, dry_run=False, snapshot=tmp_path / "s.jsonl",
                   log=lambda s: None)

    plan = load_plan(collection / "Aphelion/Nocturnes")
    assert plan.exceptions in (None, {}), "a quick take-in is not an exception for ever"
    assert embedded_cover(collection / "Aphelion/Nocturnes" / plan.tracks[0].filename) is None


def test_the_page_can_run_the_whole_pass(collection, service, tmp_path):
    """`Service.take_in_all` is what the page's Take-in dialog calls (§9, slice 101).

    The switches arrive as the page sends them, and the library itself is an allowed root — a
    collection that is already where it belongs is exactly what this pass is for.
    """
    done = service.take_in_all(collection, names="keep", musicbrainz=False, lyrics=False,
                               cover_beside=True, cover_embedded=False, lyrics_embedded=False,
                               tags=True, dry_run=True)
    assert done.status == "ok"
    assert "3 album(s), 6 track(s) would be taken in" in done.message

    # and it is refused where it would be pointless or harmful
    from noaap.service import refuse_folder
    assert refuse_folder(collection, collection, itself=True) == ""
    assert refuse_folder(collection, collection) == "that is the library itself"
    assert refuse_folder(collection / "Aphelion", collection, itself=True) == ""
    assert "inside the library" in refuse_folder(collection / "Aphelion", collection)
