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
