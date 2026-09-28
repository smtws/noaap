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
