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

from noaap import adopt, intake, precautions, sources
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

COVER = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300"
                      + "01" * 64 + "ffd9")


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


def test_the_covers_it_writes_are_counted_and_the_dry_run_says_so(collection, service, tmp_path):
    """The pass reports what it did per kind — including covers, which it could not do before.

    The number is the albums that were given a cover beside them, taken from the picture inside their
    own files. The dry run says which albums would be asked, not how many pictures it will find:
    knowing that is the same work as doing it.
    """
    from noaap.download import save_plan
    from noaap.tag import tag_file

    first = next(iter(sorted((collection / "Aphelion" / "Nocturnes").glob("*.opus"))))
    found = adopt.examine(first.parent, sources.get("folder", service.cfg), collection)
    tag_file(first, found.plan, found.plan.tracks[0], cover=COVER)
    save_plan(found.plan, first.parent)     # so the next pass reads the album as adopted

    said = []
    dry = intake.take_in(service, collection, QUIET, dry_run=True, log=said.append)
    assert dry.covers == 3, dry.would
    assert any("would be asked for a cover" in line for line in said)

    done = intake.take_in(service, collection, QUIET, dry_run=False,
                          snapshot=tmp_path / "snap.jsonl", log=lambda s: None)
    assert done.covers == 1, "one album's files held a picture; the other two held none"
    assert (collection / "Aphelion" / "Nocturnes" / "cover.jpg").read_bytes() == COVER
    assert not list((collection / "Aphelion" / "Vigil").glob("cover.*"))


def test_a_real_run_counts_what_it_did(collection, service, tmp_path):
    """`renamed: 0, retagged: 0` is what the first measured run over 2000 files reported."""
    done = intake.take_in(service, collection, QUIET, dry_run=False,
                          snapshot=tmp_path / "snap.jsonl", log=lambda s: None)

    assert done.tracks == 6
    assert done.renamed == 6, "every file got noaap's name"
    assert done.retagged == 6, "and every file was rewritten"


def test_the_kept_original_is_filed_where_the_snapshot_will_look(tmp_path, one_second_of_sound):
    """An album the pass moves into noaap's scheme is still kept under the name it came in with.

    `restore` looks for `kept / <the recorded path>`; the pass moves the album folder before the
    first file is written, so the copy was filed under the new folder's name and the restore fell
    back to putting the tags back. Measured on the user's own collection: 295 of 2000 files came back
    from their tags rather than byte for byte, and one album's originals under a name no snapshot had
    ever seen.
    """
    library = tmp_path / "collection"
    folder = library / "aphelion" / "nocturnes (2003 reissue)"     # not the scheme's spelling
    folder.mkdir(parents=True)
    for n, title in enumerate(["First", "Second"], 1):
        path = folder / f"{n:02d} {title}.opus"
        shutil.copy(one_second_of_sound, path)
        audio = MFile(path)
        audio["title"] = [title]
        audio["artist"] = ["Aphelion"]
        audio["album"] = ["Nocturnes"]
        audio.save()
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    service = Service(cfg, library, log=lambda s: None)
    kept = tmp_path / "originals"
    before = {str(p.relative_to(library)): p.read_bytes() for p in folder.glob("*.opus")}

    snapshot = tmp_path / "snap.jsonl"
    intake.take_in(service, library, intake.Choices(names="scheme", musicbrainz=False, lyrics=False),
                   dry_run=False, snapshot=snapshot, keep=kept, log=lambda s: None)

    assert not folder.exists(), "the album moved into the scheme"
    for name, bytes_before in before.items():
        assert (kept / name).read_bytes() == bytes_before, f"{name} is kept where the snapshot looks"

    done = precautions.restore(precautions.read(snapshot), library, apply=True, kept=kept,
                               log=lambda s: None)
    assert done.missing == [] and done.changed == []
    assert {str(p.relative_to(library)): p.read_bytes()
            for p in folder.glob("*.opus")} == before, "byte for byte, from the kept originals"


def test_another_collections_resume_file_is_not_this_ones(collection, service, tmp_path):
    """Two snapshots can share a directory; one resume file between them took in nothing.

    Found while measuring: a second copy of the same collection, snapshot beside the first, read the
    first's state, decided all 133 albums were done and finished in a tenth of a second — reporting
    `0 albums` as if that were an answer. The file is named after its snapshot now, and the root it
    was written for is read back and checked.
    """
    snapshot = tmp_path / "mine.jsonl"
    assert intake.state_path(snapshot).name == "mine.take-in.json", "named after its own snapshot"

    somebody_else = intake.state_path(tmp_path / "theirs.jsonl")
    intake.write_state(somebody_else, tmp_path / "another-collection",
                       {str(p.relative_to(collection)) for p in intake.albums_under(
                           collection, sources.get("folder", service.cfg))})
    assert intake.read_state(somebody_else, tmp_path / "another-collection"), "theirs, for their root"
    assert intake.read_state(somebody_else, collection) == set(), "and nothing at all for this one"

    done = intake.take_in(service, collection, QUIET, dry_run=False, snapshot=tmp_path / "theirs.jsonl",
                          log=lambda s: None)
    assert (done.adopted, done.tracks) == (3, 6), "every album taken in, none skipped"


@pytest.fixture
def two_discs(tmp_path, one_second_of_sound):
    """One album in two disc folders, with a track of the same name on both — as the collection has."""
    library = tmp_path / "collection"
    for disc, titles in ((1, ["Opening", "Shared"]), (2, ["Closing", "Shared"])):
        folder = library / "Aphelion" / "Live im Winter" / f"cd{disc}"
        folder.mkdir(parents=True)
        for n, title in enumerate(titles, 1):
            path = folder / f"{n:02d} {title}.opus"
            shutil.copy(one_second_of_sound, path)
            audio = MFile(path)
            audio["title"] = [title]
            audio["artist"] = ["Aphelion"]
            audio["album"] = ["Live im Winter"]
            audio["discnumber"] = [str(disc)]
            audio["comment"] = [f"disc {disc} track {n}"]      # so no two files are byte-equal
            audio.save()
    return library


def test_a_disc_folder_keeps_its_originals_under_their_own_paths(two_discs, tmp_path):
    """Four files in two disc folders, four kept originals — and the album moves on top of that.

    Measured on the user's own collection: filing the copy under the *album's* recorded folder
    dropped the disc subfolder for 67 of 2000 files, and where two discs held a track of the same
    name it mapped both onto one kept copy — so 3 originals were never kept at all, and nothing said
    so. The restore then put those files back from their tags instead of byte for byte.
    """
    library = two_discs
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    service = Service(cfg, library, log=lambda s: None)
    kept, snapshot = tmp_path / "originals", tmp_path / "snap.jsonl"
    before = {str(p.relative_to(library)): p.read_bytes()
              for p in sorted(library.rglob("*.opus"))}
    assert len(before) == 4

    intake.take_in(service, library, intake.Choices(names="scheme", musicbrainz=False, lyrics=False),
                   dry_run=False, snapshot=snapshot, keep=kept, log=lambda s: None)

    assert sorted(str(p.relative_to(kept)) for p in kept.rglob("*.opus")) == sorted(before), \
        "every original, under the path the snapshot wrote down"
    for name, bytes_before in before.items():
        assert (kept / name).read_bytes() == bytes_before

    done = precautions.restore(precautions.read(snapshot), library, apply=True, kept=kept,
                               log=lambda s: None)
    assert (done.missing, done.changed, done.lost) == ([], [], [])
    assert {str(p.relative_to(library)): p.read_bytes()
            for p in sorted(library.rglob("*.opus"))} == before, "byte for byte, all four"


def test_the_dry_run_counts_every_file_the_pass_rewrites(collection, service, tmp_path):
    """The two runs' arithmetic has to agree, not just their lines (§9, slice 85).

    Measured: the dry run said 1896 audio files would be rewritten and the pass then rewrote 2000.
    The lines were all there — the counter only looked for *would be retagged* and missed the 104
    that said *would be rewritten with the same tag values*, which is a rewrite too.
    """
    # a pass has been here, and the plans it wrote are gone — so every value in every file is
    # already the one noaap wants and only the record of them is missing. That is the case whose
    # lines say "would be rewritten with the same tag values", and the one the counter missed.
    intake.take_in(service, collection, QUIET, dry_run=False, snapshot=tmp_path / "first.jsonl",
                   log=lambda s: None)
    for plan in collection.rglob(".ytalbum.json"):
        plan.unlink()

    dry = intake.take_in(service, collection, QUIET, dry_run=True, log=lambda s: None)
    real = intake.take_in(service, collection, QUIET, dry_run=False,
                          snapshot=tmp_path / "second.jsonl", log=lambda s: None)

    assert real.retagged == 6, "every file is rewritten, because the plan cannot say it need not be"
    assert (dry.renamed, dry.retagged) == (real.renamed, real.retagged), \
        f"dry said {dry.renamed}/{dry.retagged}, the pass did {real.renamed}/{real.retagged}"


def test_a_disc_folder_the_pass_empties_is_removed_and_one_with_anything_in_it_is_not(tmp_path,
                                                                                      one_second_of_sound):
    """Bringing a two-disc album to the scheme takes every file out of `CD 1` and `CD 2`.

    Measured on a two-disc album before this: both folders still standing, empty. They are the pass's
    own mess, so the pass clears them — but only when they are empty, and `rmdir` is what decides
    that, so a folder holding the owner's scan of the booklet keeps the scan and keeps the folder.
    A restore puts the files back under their recorded paths, which makes the folder again.
    """
    library = tmp_path / "collection"
    for disc, titles in ((1, ["Opening"]), (2, ["Closing"])):
        folder = library / "Aphelion" / "Live im Winter" / f"CD {disc}"
        folder.mkdir(parents=True)
        for n, title in enumerate(titles, 1):
            path = folder / f"{n:02d} {title}.opus"
            shutil.copy(one_second_of_sound, path)
            audio = MFile(path)
            audio["title"] = [title]
            audio["artist"] = ["Aphelion"]
            audio["album"] = ["Live im Winter"]
            audio["discnumber"] = [str(disc)]
            audio["comment"] = [f"disc {disc}"]
            audio.save()
    theirs = library / "Aphelion" / "Live im Winter" / "CD 2" / "booklet.jpg"
    theirs.write_bytes(b"\xff\xd8 their scan")
    cfg = Config(library_root=library, musicbrainz=False, lyrics=False)
    service = Service(cfg, library, log=lambda s: None)
    snapshot = tmp_path / "snap.jsonl"

    intake.take_in(service, library, intake.Choices(names="scheme", musicbrainz=False, lyrics=False),
                   dry_run=False, snapshot=snapshot, keep=tmp_path / "kept", log=lambda s: None)

    album = library / "Aphelion" / "Live im Winter"
    assert not (album / "CD 1").exists(), "emptied by the pass, so the pass takes it away"
    assert theirs.read_bytes() == b"\xff\xd8 their scan", "and this one is still in CD 2"

    precautions.restore(precautions.read(snapshot), library, apply=True, kept=tmp_path / "kept",
                        log=lambda s: None)
    assert (album / "CD 1" / "01 Opening.opus").is_file(), "the folder is back, with its file in it"


def test_a_restore_takes_away_what_the_pass_itself_put_there(collection, service, tmp_path):
    """R-342 ruling 3: "a way back" means the folder as it was, not the folder plus our leavings.

    The pass writes one plan per album, and saves any cover it fetched and any words it found. A
    restore that only put files back left all of that behind: measured on the user's own 2000 files,
    the restored copy held **136 files its owner never had**. So the pass records what it creates as
    it goes, and the restore removes exactly those paths and nothing else — listing every one of them
    in its dry run first.
    """
    snapshot = tmp_path / "snap.jsonl"
    theirs = next(iter(sorted(collection.rglob("*.opus")))).parent / "booklet.jpg"
    theirs.write_bytes(b"\xff\xd8 their scan")

    intake.take_in(service, collection, QUIET, dry_run=False, snapshot=snapshot, log=lambda s: None)

    made = intake.read_made(intake.made_path(snapshot), collection)
    assert made and all(name.endswith(".ytalbum.json") for name in made), made
    assert len(made) == 3, "one plan per album, and nothing of theirs"

    said = []
    dry = precautions.restore(precautions.read(snapshot), collection, apply=False, made=made,
                              log=said.append)
    assert len(dry.removed) == 3
    assert sum(1 for line in said if "would remove" in line) == 3, said
    assert all((collection / name).is_file() for name in made), "a dry run removes nothing"

    done = precautions.restore(precautions.read(snapshot), collection, apply=True, made=made,
                               log=lambda s: None)
    assert sorted(done.removed) == sorted(made)
    assert not list(collection.rglob(".ytalbum.json")), "the plans are gone"
    assert theirs.read_bytes() == b"\xff\xd8 their scan", "and what was theirs is untouched"
    assert len(list(collection.rglob("*.opus"))) == 6, "every track still here"


def test_the_record_of_another_collection_is_not_read(collection, service, tmp_path):
    """The same rule as the resume file: a record names the root it was written for."""
    snapshot = tmp_path / "snap.jsonl"
    intake.write_made(intake.made_path(snapshot), tmp_path / "somewhere-else", ["a/plan.json"])

    assert intake.read_made(intake.made_path(snapshot), tmp_path / "somewhere-else")
    assert intake.read_made(intake.made_path(snapshot), collection) == []
