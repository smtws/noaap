"""A collection becomes a library where it stands (DESIGN §9, slice 58).

The rule the whole package turns on: **an adopted album keeps its own names and its own tags**, and
renaming or retagging it is a separate thing a person asks for. The case that proves why is the
first one here — without it an ordinary pass renames an mp3 to `.opus` and then cannot read it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from test_folder_source import encode

from noaap import sources
from noaap.config import Config
from noaap.plan import build_plan


@pytest.fixture
def collection(tmp_path) -> Path:
    """One album in each of the three containers the reference collection is made of."""
    album = tmp_path / "A Band" / "An Album"
    for n, (title, suffix) in enumerate([("One", ".mp3"), ("Two", ".flac"), ("Three", ".opus")], 1):
        encode(album / f"{n:02d} - {title}{suffix}", title=title, artist="A Band",
               album="An Album", album_artist="A Band", track=str(n))
    return album


def folder():
    return sources.get("folder", Config())


def test_a_track_knows_what_container_it_is_in(collection):
    """The file is already on the disk, so the container is known now — not after a download that
    never happens. `PlanTrack.ext` defaulted to `opus`, and over the real collection that would
    have renamed **1662 of 2000 files** (932 mp3, 730 flac) into a name that lies, after which our
    own tagger cannot open them (`read b'ID3', expected b'OggS'`)."""
    plan = build_plan(folder().collection(str(collection)), source=folder())

    assert [t.ext for t in plan.tracks] == ["mp3", "flac", "opus"]
    assert [Path(t.video_id).suffix.lstrip(".") for t in plan.tracks] == ["mp3", "flac", "opus"]


def test_the_name_the_plan_would_use_keeps_the_container(collection):
    plan = build_plan(folder().collection(str(collection)), source=folder())

    assert [Path(t.filename).suffix for t in plan.tracks] == [".mp3", ".flac", ".opus"]


def test_a_source_that_cannot_know_yet_says_nothing(collection):
    """A download finds out when the file arrives, and `None` is how a source says so — the core
    must never read a container off a ref, which is opaque to it."""
    from noaap.models import Entry, PlanTrack

    assert Entry(video_id="x", position=1, title="t").ext is None
    assert PlanTrack(video_id="x", number=1, artist="a", title="t", filename="f",
                     provenance={}).ext == "opus", "and the download default is unchanged"


# -- the two flags that make an album stay the collection's ---------------------------------------


def adopted(album: Path, library: Path):
    """What commit 3 will write: a plan that says these names and these tags are the owner's."""
    from noaap.download import save_plan

    plan = build_plan(folder().collection(str(album)), source=folder())
    plan.provider = "folder"
    plan.keep_names = plan.keep_tags = True
    plan.folder = str(album.relative_to(library))
    for t in plan.tracks:
        t.state = "done"
        t.filename = Path(t.video_id).name
        t.adopted_name = t.filename
    save_plan(plan, album)
    return plan


def digests(album: Path) -> dict[str, str]:
    from noaap.sources_folder import AUDIO, stream_sha

    return {p.name: stream_sha(p) for p in sorted(album.iterdir()) if p.suffix.lower() in AUDIO}


def test_an_ordinary_pass_leaves_an_adopted_album_exactly_as_it_is(collection, tmp_path):
    """The whole promise, in one case. Without `keep_names` this renames all three files and turns
    the mp3 into `.opus`; without `keep_tags` it rewrites every one of them."""
    from noaap.download import run
    from noaap.sources_folder import AUDIO

    plan = adopted(collection, tmp_path)
    audio = lambda: {p.name: (p.stat().st_mtime_ns, p.stat().st_size)  # noqa: E731
                     for p in collection.iterdir() if p.suffix.lower() in AUDIO}
    before, sounds = audio(), digests(collection)

    run(plan, collection, folder(), track_source=lambda t: folder(), download=False)

    assert audio() == before, "not one file renamed, and not one byte written"
    assert digests(collection) == sounds
    assert (collection / ".ytalbum.json").is_file(), "the plan is ours and is written; nothing else is"


def test_the_folder_stays_where_its_owner_put_it(collection, tmp_path):
    """`relocate` would move "A Band/An Album" to wherever the derived names point."""
    from noaap.download import relocate

    plan = adopted(collection, tmp_path)

    assert relocate(collection, plan, tmp_path) == collection
    assert collection.is_dir()


def test_deriving_names_is_skipped_entirely(collection, tmp_path):
    """`refresh_derived` is what every later pass calls, and it is where the wanting starts."""
    from noaap.plan import refresh_derived

    plan = adopted(collection, tmp_path)
    names = [t.filename for t in plan.tracks]

    refresh_derived(plan)

    assert [t.filename for t in plan.tracks] == names
    assert plan.folder == str(collection.relative_to(tmp_path))


def test_an_album_noaap_fetched_is_untouched_by_any_of_this(collection, tmp_path):
    """Both flags default to false, so nothing that exists today behaves differently."""
    from noaap.download import run

    plan = build_plan(folder().collection(str(collection)), source=folder())
    plan.provider = "folder"
    assert plan.keep_names is False and plan.keep_tags is False
    for t in plan.tracks:
        t.state = "done"
        t.filename = Path(t.video_id).name

    run(plan, collection, folder(), track_source=lambda t: folder(), download=False)

    assert [t.filename for t in plan.tracks] != [Path(t.video_id).name for t in plan.tracks], \
        "an ordinary album is still renamed into noaap's scheme"
    assert (collection / plan.tracks[0].filename).is_file()


# -- adopting: one plan per album, and nothing else ------------------------------------------------


@pytest.fixture
def library(tmp_path, collection) -> Path:
    """A collection root with two artists, so scope can be narrowed."""
    other = tmp_path / "B Band" / "Another"
    encode(other / "01 - Song.flac", title="Song", artist="B Band", album="Another",
           album_artist="B Band", track="1")
    return tmp_path


def survey_of(library: Path, **kw):
    from noaap import adopt

    return adopt.survey(library, library, folder(), **kw)


def test_a_dry_adoption_writes_nothing(library):
    before = {p: p.stat().st_mtime_ns for p in sorted(library.rglob("*")) if p.is_file()}

    found = survey_of(library)

    assert found.counts() == {"albums": 2, "tracks": 4, "refused": 0}
    assert {p: p.stat().st_mtime_ns for p in sorted(library.rglob("*")) if p.is_file()} == before


def test_adopting_adds_one_file_per_album_and_no_other(library):
    from noaap import adopt
    from noaap.download import PLAN_FILE

    before = {p for p in library.rglob("*") if p.is_file()}

    done = adopt.carry_out(survey_of(library))

    after = {p for p in library.rglob("*") if p.is_file()}
    assert done == {"adopted": 2, "tracks": 4}
    assert {p.name for p in after - before} == {PLAN_FILE}, "the plan, and nothing else"
    assert len(after - before) == 2


def test_the_plan_says_the_album_is_the_collections(library):
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    plan = load_plan(library / "A Band" / "An Album")

    assert plan.keep_names and plan.keep_tags
    assert plan.provider == "folder"
    assert plan.folder == "A Band/An Album", "where its owner put it"
    assert all(t.state == "done" for t in plan.tracks)
    assert [t.filename for t in plan.tracks] == [Path(t.video_id).name for t in plan.tracks]


def test_what_the_file_said_is_recorded_before_anything_could_change_it(library):
    """The undo data, written by the same act that adopts — nothing else ever sees these values
    again once a retag has run."""
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    track = load_plan(library / "A Band" / "An Album").tracks[0]

    assert track.adopted_name == Path(track.video_id).name
    assert track.adopted_tags["title"] == "One"
    assert track.adopted_tags["albumartist"] == "A Band"
    assert "discnumber" in track.adopted_tags and track.adopted_tags["discnumber"] is None, \
        "absent is a value too: without it an undo leaves behind a tag the owner never had"


def test_an_album_that_is_already_ours_is_left_alone(library):
    from noaap import adopt

    adopt.carry_out(survey_of(library))
    again = survey_of(library)

    assert again.counts() == {"albums": 0, "tracks": 0, "refused": 2}
    assert all(a.refused == "already a noaap album" for a in again.refused)


def test_a_folder_that_is_two_albums_is_named_and_left(tmp_path):
    """The owner made this folder; deciding which files are one album is theirs, not ours."""
    mixed = tmp_path / "Someone" / "Mixed"
    encode(mixed / "01 - A.mp3", title="A", artist="Someone", album="First", track="1")
    encode(mixed / "02 - B.mp3", title="B", artist="Someone", album="Second", track="2")

    found = survey_of(tmp_path)

    assert found.counts()["albums"] == 0
    assert "2 different albums by their own tags" in found.refused[0].refused


@pytest.mark.parametrize("narrow,albums", [
    ({}, 2), ({"artist": "A Band"}, 1), ({"album": "Another"}, 1), ({"artist": "Nobody"}, 0),
])
def test_the_scope_reads_as_the_folders_do(library, narrow, albums):
    assert survey_of(library, **narrow).counts()["albums"] == albums


# -- giving it back ---------------------------------------------------------------------------------


def fingerprint(library: Path) -> dict[str, tuple[str, dict[str, str | None]]]:
    """Every audio file by name, with its stream digest and the fields noaap would ever write."""
    from noaap.adopt import WRITTEN
    from noaap.sources_folder import AUDIO, stream_sha
    from noaap.tag import raw_tags

    return {str(p.relative_to(library)): (stream_sha(p), raw_tags(p, WRITTEN))
            for p in sorted(library.rglob("*")) if p.suffix.lower() in AUDIO}


def test_an_undo_gives_back_every_name_and_every_field(library):
    """R-189, ruling 4. Judged on what matters: the names, the fields, and the audio — **not** on
    byte identity of the tag block, which mutagen cannot give back and this does not claim."""
    from noaap import adopt
    from noaap.download import PLAN_FILE, load_plan

    before = fingerprint(library)
    files_before = {p for p in library.rglob("*") if p.is_file()}
    adopt.carry_out(survey_of(library))

    for album_dir in (library / "A Band" / "An Album", library / "B Band" / "Another"):
        adopt.give_back(album_dir, load_plan(album_dir), library)

    assert fingerprint(library) == before, "name for name, field for field, stream for stream"
    assert {p for p in library.rglob("*") if p.is_file()} == files_before, "and nothing of ours left"
    assert not list(library.rglob(PLAN_FILE))


def test_an_undo_after_a_rename_puts_the_names_back(library):
    from noaap import adopt
    from noaap.download import load_plan

    before = fingerprint(library)
    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    plan = load_plan(album_dir)
    for track in plan.tracks:  # what `--rename` will do in the next commit
        (album_dir / track.filename).rename(album_dir / f"renamed {track.filename}")
        track.filename = f"renamed {track.filename}"

    adopt.give_back(album_dir, plan, library)

    assert fingerprint(library) == before


def test_a_file_noaap_added_and_the_user_edited_is_kept(library):
    """It is theirs now, whatever put it there."""
    from noaap import adopt
    from noaap.download import load_plan
    from noaap.lyrics import sidecar_path

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    plan = load_plan(album_dir)
    words = sidecar_path(album_dir, plan.tracks[0].filename)
    words.write_text("[00:01.00] as noaap wrote it\n", encoding="utf-8")
    adopt.remember_added(album_dir, plan)
    words.write_text("[00:01.00] as the user fixed it\n", encoding="utf-8")

    said: list[str] = []
    done = adopt.give_back(album_dir, load_plan(album_dir), library, log=said.append)

    assert words.is_file() and "fixed it" in words.read_text()
    assert done["kept"] == 1 and any("edited since" in line for line in said)


def test_an_undo_without_a_record_refuses_rather_than_guesses(library):
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    plan = load_plan(album_dir)
    plan.adopted = {}

    with pytest.raises(ValueError, match="no record of an adoption"):
        adopt.give_back(album_dir, plan, library)
