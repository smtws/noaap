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
    # in the container's own spelling — this one is an mp3, so ID3 frames
    assert track.adopted_tags["TIT2"] == ["One"]
    assert track.adopted_tags["TPE2"] == ["A Band"]
    assert "TPOS" not in track.adopted_tags, \
        "a key the file does not carry is absent from the record, which is how an undo knows to " \
        "remove it rather than write an empty one"


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
    from noaap.sources_folder import AUDIO, stream_sha
    from noaap.tag import raw_tags

    return {str(p.relative_to(library)): (stream_sha(p), raw_tags(p))
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


def test_a_sidecar_the_user_edited_is_kept_and_one_noaap_wrote_is_not(library):
    """**Every file noaap writes records its own fingerprint as it writes it** — a sidecar answers
    to `lyrics_sha`, "the bytes we wrote; anything else is the user's". A snapshot taken at
    adoption instead is stale the moment a later pass writes one, which is how the first live undo
    kept 80 sidecars noaap had written itself."""
    from noaap import adopt
    from noaap.download import load_plan, save_plan
    from noaap.lyrics import sidecar_path, sidecar_sha

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    plan = load_plan(album_dir)
    ours, theirs = plan.tracks[0], plan.tracks[1]
    for track in (ours, theirs):
        sidecar_path(album_dir, track.filename).write_text("[00:01.00] words\n", encoding="utf-8")
    ours.lyrics_sha = sidecar_sha(album_dir, ours)      # written by a pass, which records it
    sidecar_path(album_dir, theirs.filename).write_text("[00:01.00] mine\n", encoding="utf-8")
    save_plan(plan, album_dir)

    said: list[str] = []
    done = adopt.give_back(album_dir, load_plan(album_dir), library, log=said.append)

    assert not sidecar_path(album_dir, ours.filename).exists(), "ours goes"
    assert sidecar_path(album_dir, theirs.filename).is_file(), "theirs stays"
    assert done["kept"] == 1 and any("not the file noaap wrote" in line for line in said)


def test_an_undo_without_a_record_refuses_rather_than_guesses(library):
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    plan = load_plan(album_dir)
    plan.adopted = {}

    with pytest.raises(ValueError, match="no record of an adoption"):
        adopt.give_back(album_dir, plan, library)


# -- the two things a person may ask for afterwards ---------------------------------------------------


def test_renaming_gives_the_files_noaaps_names_and_leaves_the_folder(library):
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    plan = load_plan(album_dir)

    moved = adopt.rename(album_dir, plan)

    assert moved == 3
    assert all(t.filename.startswith("A Band - An Album - ") for t in plan.tracks)
    assert [Path(t.filename).suffix for t in plan.tracks] == [".mp3", ".flac", ".opus"], \
        "and every one keeps the container it actually is"
    assert album_dir.is_dir(), "the folder stays where its owner put it"
    assert plan.keep_names is False, "from here the names are noaap's"


def test_retagging_keeps_what_this_program_does_not_model(library):
    """A collection somebody has been tagging for years holds fields we know nothing about, and
    losing them would be the adoption destroying the thing it took in."""
    import mutagen

    from noaap import adopt
    from noaap.download import load_plan

    album_dir = library / "A Band" / "An Album"
    flac = next(album_dir.glob("*.flac"))
    audio = mutagen.File(flac)
    audio["replaygain_track_gain"], audio["composer"] = ["-6.66 dB"], ["Somebody Else"]
    audio.save()
    adopt.carry_out(survey_of(library))
    plan = load_plan(album_dir)

    written = adopt.retag(album_dir, plan)

    after = mutagen.File(flac)
    assert written == 3
    assert after["replaygain_track_gain"] == ["-6.66 dB"] and after["composer"] == ["Somebody Else"]
    assert after["albumartist"] == ["A Band"], "and the plan's own fields are in"
    assert plan.keep_tags is False


def test_an_undo_after_a_retag_puts_every_field_back(library):
    """R-189, ruling 4, the whole of it: after `--retag` and after `--undo` the names, the fields
    and the stream are the ones that were there."""
    from noaap import adopt
    from noaap.download import load_plan

    before = fingerprint(library)
    adopt.carry_out(survey_of(library))
    for album_dir in (library / "A Band" / "An Album", library / "B Band" / "Another"):
        plan = load_plan(album_dir)
        adopt.retag(album_dir, plan)
        adopt.rename(album_dir, plan)
        from noaap.download import save_plan
        save_plan(plan, album_dir)

    assert fingerprint(library) != before, "the retag really did change the files"

    for album_dir in (library / "A Band" / "An Album", library / "B Band" / "Another"):
        adopt.give_back(album_dir, load_plan(album_dir), library)

    assert fingerprint(library) == before


@pytest.mark.parametrize("act", ["rename", "retag"])
def test_neither_runs_without_a_way_back(library, act):
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    plan = load_plan(album_dir)
    plan.tracks[1].adopted_tags = None

    with pytest.raises(ValueError, match="no record of what they were"):
        getattr(adopt, act)(album_dir, plan)


# -- the album that had no tags at all --------------------------------------------------------------


@pytest.fixture
def untagged(tmp_path) -> Path:
    """One album per container whose files carry **no tags**. The reference collection has such an
    album, and the first undo crashed on it: making a field absent again is not the same code path
    as putting a value back, and nothing here had ever exercised it."""
    album = tmp_path / "Nobody" / "Untitled"
    for n, suffix in enumerate((".mp3", ".flac", ".opus"), 1):
        encode(album / f"{n:02d} - track{suffix}")
    return album


@pytest.mark.parametrize("suffix", [".mp3", ".flac", ".opus"])
def test_a_file_with_no_tags_has_no_tags_again(untagged, tmp_path, suffix):
    """R-192, item 1. `pop` does not exist on a Vorbis comment block — not even with one argument —
    so removing a key had to be `del`, and three containers' worth of cases passed without it
    because every file in them had something to put back."""
    from noaap import adopt
    from noaap.download import load_plan, save_plan
    from noaap.tag import raw_tags

    audio = next(untagged.glob(f"*{suffix}"))
    assert raw_tags(audio) == {}, "it starts with nothing"

    adopt.carry_out(adopt.survey(tmp_path, tmp_path, folder()))
    plan = load_plan(untagged)
    adopt.retag(untagged, plan)
    save_plan(plan, untagged)
    assert raw_tags(audio), "the retag really did write fields"

    adopt.give_back(untagged, load_plan(untagged), tmp_path)

    assert raw_tags(audio) == {}, "and now it says nothing again"


def test_one_track_that_cannot_be_given_back_costs_that_track(untagged, tmp_path):
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(adopt.survey(tmp_path, tmp_path, folder()))
    plan = load_plan(untagged)
    plan.tracks[1].adopted_name = None  # the record for this one is gone

    done = adopt.give_back(untagged, plan, tmp_path, log=lambda s: None)

    assert done["failed"] == 1
    assert (untagged / ".ytalbum.json").is_file(), \
        "the plan stays: it is the only record of what the other files were"


def test_an_undo_can_be_run_again_and_finishes_what_is_left(library, untagged, tmp_path, monkeypatch):
    """R-192, item 3. The state after a failure is not a dead end."""
    from noaap import adopt
    from noaap.download import PLAN_FILE, load_plan

    before = fingerprint(tmp_path)
    adopt.carry_out(adopt.survey(tmp_path, tmp_path, folder()))

    # the first pass: one album cannot be finished, the others are. The failure is the real one —
    # the container refusing to remove a key — and it goes away without touching the record.
    real = adopt.restore_tags
    refused: list[Path] = []

    def refuses(path, values):
        if "Untitled" in str(path) and not refused:
            refused.append(path)
            raise TypeError("pop expected at most 1 argument, got 2")
        return real(path, values)

    monkeypatch.setattr(adopt, "restore_tags", refuses)
    first = {}
    for album_dir in (untagged, tmp_path / "A Band" / "An Album", tmp_path / "B Band" / "Another"):
        first[album_dir.name] = adopt.give_back(album_dir, load_plan(album_dir), tmp_path,
                                                log=lambda s: None)

    assert first["Untitled"]["failed"] == 1 and (untagged / PLAN_FILE).is_file()
    assert not (tmp_path / "A Band" / "An Album" / PLAN_FILE).exists(), "the others were finished"

    # and again, with nothing reset
    monkeypatch.setattr(adopt, "restore_tags", real)
    again = adopt.give_back(untagged, load_plan(untagged), tmp_path, log=lambda s: None)

    assert again["failed"] == 0
    assert not list(tmp_path.rglob(PLAN_FILE)), "nothing of noaap's is left anywhere"
    assert fingerprint(tmp_path) == before


# -- the record has to know every key a writer can write -----------------------------------------------


def test_the_record_covers_every_key_the_writers_can_write():
    """R-194, ruling 1. The first record was a hand-written list of seven fields; the retag wrote
    `tracktotal` and `totaltracks` as well, the undo had never heard of them, and 17 files came
    back carrying tags their owner never had.

    So this reads **the writers' own source** for the keys they set outside their key maps. It
    fails when a writer gains one, which is the only way a list like this stays true.
    """
    import inspect
    import re

    from noaap import tag

    source = inspect.getsource(tag)
    # every `id3.setall("X"` / `audio["X"] =` in the three writers, and the two key maps
    literal = set(re.findall(r'(?:setall|delall)\(\s*"([^"]+)"', source))
    literal |= set(re.findall(r'audio\[\s*"([^"]+)"\s*\]\s*=', source))
    literal |= {k for k in re.findall(r'audio\[\s*([A-Z_]+)\s*\]\s*=', source)}
    known = set(tag.WRITES["mp3"]) | set(tag.WRITES["mp4"]) | set(tag.WRITES["opus"])
    known |= {"PICTURE_KEY", "APIC", "covr"}  # pictures are not a tag one puts back from a record

    assert literal <= known, f"a writer sets keys the undo does not know: {sorted(literal - known)}"


def test_the_logical_keys_come_from_build_tags_itself():
    """The other half: the vorbis spelling *is* whatever `build_tags` returns, so it cannot drift."""
    from noaap.models import AlbumPlan, Kind, PlanTrack
    from noaap.tag import WRITES, build_tags

    one = PlanTrack(video_id="v", number=1, artist="a", title="t", filename="f", provenance={},
                    disc=1, mbid="x")
    two = PlanTrack(video_id="w", number=2, artist="a", title="u", filename="g", provenance={}, disc=2)
    plan = AlbumPlan(source_url="https://example.invalid/1", source_id="s", kind=Kind.COMPILATION,
                     album="al", albumartist="aa", year=2000, cover_url=None, folder="f",
                     tracks=[one, two], mbid="y")

    assert set(build_tags(plan, one, "words")) <= set(WRITES["opus"])
    for key in ("tracktotal", "totaltracks", "compilation", "source", "youtube_id", "lyrics",
                "musicbrainz_albumid", "musicbrainz_trackid", "discnumber", "date"):
        assert key in WRITES["opus"], f"{key} is written and the undo must know it"


@pytest.mark.parametrize("suffix", [".mp3", ".flac", ".opus"])
def test_a_retag_adds_nothing_the_undo_cannot_take_away(untagged, tmp_path, suffix):
    """The failure itself, as a case: compare the **whole** tag set, not a chosen list."""
    from noaap import adopt
    from noaap.download import load_plan, save_plan
    from noaap.tag import raw_tags

    audio = next(untagged.glob(f"*{suffix}"))
    before = raw_tags(audio)

    adopt.carry_out(adopt.survey(tmp_path, tmp_path, folder()))
    plan = load_plan(untagged)
    adopt.retag(untagged, plan)
    save_plan(plan, untagged)
    adopt.give_back(untagged, load_plan(untagged), tmp_path)

    assert raw_tags(audio) == before


# -- reading an adopted album again, when its owner has changed it ---------------------------------


def reread(library: Path, album_dir: Path):
    from noaap.config import Config
    from noaap.service import Service

    return Service(Config(musicbrainz=False, lyrics=False), library, log=lambda s: None).reread(album_dir)


def test_a_new_file_in_an_adopted_album_becomes_a_track_and_nothing_is_copied(library):
    """The library shape: the folder **is** the album, so a file added to it is a new track — the
    opposite of intake, which reports an album it already has and stops."""
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    before = digests(album_dir)
    encode(album_dir / "04 - Four.mp3", title="Four", artist="A Band", album="An Album", track="4")

    reread(library, album_dir)

    plan = load_plan(album_dir)
    assert len(plan.tracks) == 4 and "Four" in [t.title for t in plan.tracks]
    assert digests(album_dir) | {} == {**before, **digests(album_dir)}, "nothing existing was touched"
    assert plan.keep_names and plan.keep_tags, "it is still the collection's"


def test_a_second_copy_of_a_track_joins_it_rather_than_replacing_it(library):
    """Measured on the real collection first: `fetch` on an album's own folder took the new file as
    the track's best copy, read it as a new track, and the download half wrote it over the old
    one's name. `reread` keeps the track, its ref and its file, and the new file is a candidate."""
    import shutil

    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    first = load_plan(album_dir).tracks[0]
    shutil.copy2(album_dir / first.filename, album_dir / "another copy.mp3")

    reread(library, album_dir)

    plan = load_plan(album_dir)
    assert len(plan.tracks) == 3, "three tracks, not four: it is the same recording"
    assert plan.tracks[0].filename == first.filename, "and it keeps the file it had"
    assert "another copy.mp3" in [Path(c.ref).name for c in plan.tracks[0].candidates]


def test_a_file_the_owner_removed_is_recorded_and_nothing_is_deleted(library):
    """R-199, decision 3: a file that disappears is a fact in the plan, never a reason to remove."""
    from noaap import adopt
    from noaap.download import load_plan

    adopt.carry_out(survey_of(library))
    album_dir = library / "A Band" / "An Album"
    plan = load_plan(album_dir)
    (album_dir / plan.tracks[2].filename).unlink()

    reread(library, album_dir)

    after = load_plan(album_dir)
    assert len(after.tracks) == 3, "the track stays in the plan"
    assert [t.in_source for t in after.tracks].count(False) == 1
    assert len(list(album_dir.glob("*.mp3"))) + len(list(album_dir.glob("*.flac"))) \
        + len(list(album_dir.glob("*.opus"))) == 2, "and the other two are still there"
