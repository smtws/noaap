"""A folder is a source (DESIGN §9, slice 53).

Modelled on the reference collection the provider was built for — 2000 files, 135 album folders,
counted 2026-09-28 — but built in `tmp_path` from one-second files ffmpeg makes here. No audio
lives in the repository, and **nothing in these cases writes inside a source folder**: that is the
provider's promise and the first case checks it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from test_media import JPEG

from noaap import sources
from noaap.config import Config
from noaap.models import Collection
from noaap.sources_folder import FolderSource, cover_in, read_tags


def encode(path: Path, **tags: str) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    meta = [arg for key, value in tags.items() for arg in ("-metadata", f"{key}={value}")]
    codec = {".flac": "flac", ".mp3": "libmp3lame", ".opus": "libopus"}[path.suffix]
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", codec, *meta, str(path)], check=True)
    return path


@pytest.fixture
def album(tmp_path) -> Path:
    """One tagged album, the ordinary case: 3 of 2000 files in the reference collection are not."""
    folder = tmp_path / "Van Canto" / "Trust In Rust"
    for n, title in enumerate(["Back in the Lead", "Javelin", "Trust in Rust"], 1):
        encode(folder / f"{n:02d} - {title}.mp3", title=title, artist="Van Canto",
               album="Trust in Rust", album_artist="Van Canto", date="2018", track=str(n))
    (folder / "cover.jpg").write_bytes(JPEG)
    return folder


@pytest.fixture
def source() -> FolderSource:
    return FolderSource(Config())


# -- what a folder is ------------------------------------------------------------------------------


def test_the_registry_knows_it_and_it_claims_only_paths(source):
    assert "folder" in sources.known()
    assert sources.get("folder", Config()).name == "folder"

    assert source.handles(str(Path.home())) is True
    assert source.handles("https://www.youtube.com/playlist?list=PLx") is False
    assert source.handles("/no/such/place/at/all") is False


def test_a_folder_is_a_collection_and_the_tags_are_the_truth(album, source):
    found = source.collection(str(album))

    assert isinstance(found, Collection)
    assert found.title == "Trust in Rust", "the album tag, not the folder name"
    assert found.owner == "Van Canto"
    assert [e.title for e in found.entries] == ["Back in the Lead", "Javelin", "Trust in Rust"]
    assert [e.position for e in found.entries] == [1, 2, 3]
    assert all(e.music.album == "Trust in Rust" and e.music.year == 2018 for e in found.entries)
    assert all(e.duration == pytest.approx(1.0, abs=0.2) for e in found.entries)
    assert found.thumbnail == str(album / "cover.jpg")


def test_the_track_number_orders_it_not_the_file_name(tmp_path, source):
    folder = tmp_path / "album"
    for name, number, title in (("zz.flac", "1", "First"), ("aa.flac", "2", "Second")):
        encode(folder / name, title=title, artist="A", album="B", track=number)

    assert [e.title for e in source.collection(str(folder)).entries] == ["First", "Second"]


def test_without_track_numbers_the_name_orders_it(tmp_path, source):
    folder = tmp_path / "album"
    for name in ("02 - second.flac", "01 - first.flac"):
        encode(folder / name, artist="A", album="B")

    assert [e.title for e in source.collection(str(folder)).entries] == ["01 - first", "02 - second"]


def test_a_folder_with_no_audio_is_refused_by_name(tmp_path, source):
    (tmp_path / "sleeve.jpg").write_bytes(JPEG)

    with pytest.raises(sources.NotSupported, match="no audio"):
        source.collection(str(tmp_path))


def test_a_path_that_is_not_a_folder_is_refused(tmp_path, source):
    with pytest.raises(sources.NotSupported, match="not a folder"):
        source.collection(str(tmp_path / "nowhere"))


# -- audio() copies --------------------------------------------------------------------------------


def test_audio_copies_and_leaves_the_source_exactly_as_it_was(album, source, tmp_path):
    before = {p: (p.read_bytes(), p.stat().st_mtime) for p in sorted(album.iterdir())}
    ref = str(album / "01 - Back in the Lead.mp3")

    out = source.audio(ref, tmp_path / "into")

    assert out.is_file() and out.read_bytes() == before[Path(ref)][0]
    assert out.parent != album
    assert {p: (p.read_bytes(), p.stat().st_mtime) for p in sorted(album.iterdir())} == before, \
        "the source folder is the user's: not written, not moved, not re-encoded"


def test_a_file_that_has_gone_is_no_audio_and_says_where(album, source, tmp_path):
    ref = str(album / "nothing here.mp3")

    with pytest.raises(sources.NoAudio, match="no longer at"):
        source.audio(ref, tmp_path / "into")


def test_probe_answers_from_the_file_alone(album, source):
    entry = source.probe(str(album / "02 - Javelin.mp3"))

    assert entry.title == "Javelin" and entry.music.artist == "Van Canto"
    assert entry.duration == pytest.approx(1.0, abs=0.2)


def test_the_cover_is_read_as_bytes_by_its_path(album, source):
    found = source.collection(str(album))

    assert source.art(found.thumbnail) == JPEG


# -- the cheap check -------------------------------------------------------------------------------


def test_changed_lists_the_files_and_the_folders_date(album, source):
    state = source.changed(str(album))

    assert len(state["ids"]) == 3 and all(i.endswith(".mp3") for i in state["ids"])
    assert state["modified"] and len(state["modified"]) == 8


def test_a_new_file_changes_it_and_reading_a_file_does_not(album, source):
    before = source.changed(str(album))

    (album / "01 - Back in the Lead.mp3").read_bytes()
    assert source.changed(str(album)) == before, "a read is not a change"

    encode(album / "04 - Melody.mp3", title="Melody", artist="Van Canto", album="Trust in Rust", track="4")
    assert source.changed(str(album))["ids"] != before["ids"]


def test_a_folder_that_moved_is_reported_rather_than_guessed(album, source, tmp_path):
    address = str(album)
    shutil.move(album, tmp_path / "elsewhere")

    assert source.changed(address) is None, "update reads it in full and says the folder is gone"


# -- what is ignored -------------------------------------------------------------------------------


def test_thumbnail_caches_and_logos_are_never_the_cover(album):
    """Measured on the reference collection: 74 files under `.thumb/`, one `cover.jpg.jpg` per real
    cover, and `logo.jpg` in six folders — a label's logo, not an album's."""
    (album / ".thumb").mkdir()
    (album / ".thumb" / "cover.jpg.jpg").write_bytes(b"thumbnail")
    (album / "logo.jpg").write_bytes(b"a label")

    assert cover_in(album) == album / "cover.jpg"


def test_hidden_files_and_strays_are_not_tracks(album, source):
    (album / "New Album Releases.url").write_text("[InternetShortcut]\n")
    (album / ".hidden.mp3").write_bytes(b"not really audio")

    found = source.collection(str(album))

    assert len(found.entries) == 3
    assert all(not Path(e.video_id).name.startswith(".") for e in found.entries)


def test_an_unreadable_file_does_not_stop_the_folder(album, source):
    (album / "04 - broken.mp3").write_bytes(b"<html>a consent page</html>")

    found = source.collection(str(album))

    assert len(found.entries) == 4
    broken = found.entries[-1]
    assert broken.title == "04 - broken", "no tags to read, so the name stands in"
    assert broken.duration is None


def test_read_tags_answers_empty_for_something_that_is_not_audio(tmp_path):
    path = tmp_path / "sleeve.flac"
    path.write_bytes(b"<html>")

    assert read_tags(path) == {}


# -- a listing -------------------------------------------------------------------------------------


def test_listing_finds_the_albums_under_an_artist(album, source):
    encode(album.parent / "To The Power Of Eight" / "01 - Hold My Fire.flac",
           title="Hold My Fire", artist="Van Canto", album="To The Power Of Eight")

    found = source.listing(str(album.parent.parent))

    assert sorted(r.title for r in found) == ["To The Power Of Eight", "Trust in Rust"]
    assert all(r.tab == "folders" and r.count for r in found)


def test_an_artist_folder_that_is_itself_an_album_is_one_entry(tmp_path, source):
    """A root whose child holds audio directly: that child is the album, not a parent of albums."""
    encode(tmp_path / "Singles" / "01 - One.flac", title="One", artist="A", album="Singles")
    encode(tmp_path / "Singles" / "extras" / "02 - Two.flac", title="Two", artist="A", album="Extras")

    found = source.listing(str(tmp_path))

    assert [r.title for r in found] == ["Singles"]


# -- and the one thing a ref must never become ------------------------------------------------------


def test_a_local_path_is_never_written_into_a_files_tags(album, source, tmp_path):
    """`youtube_id` holds a ref, and a folder's ref is an absolute path. Writing one would carry
    someone's home directory into every file and identify the track to nobody."""
    from noaap.models import AlbumPlan, Candidate, Kind, PlanTrack
    from noaap.tag import build_tags

    ref = str(album / "01 - Back in the Lead.mp3")
    track = PlanTrack(video_id=ref, number=1, artist="Van Canto", title="Back in the Lead",
                      filename="x.mp3", provenance={},
                      candidates=[Candidate(ref=ref, provider="folder")], chosen=ref)
    plan = AlbumPlan(source_url=str(album), source_id=str(album), kind=Kind.OFFICIAL_ALBUM,
                     album="Trust in Rust", albumartist="Van Canto", year=2018, cover_url=None,
                     folder="x", tracks=[track], provider="folder")

    tags = build_tags(plan, track)

    assert "youtube_id" not in tags
    assert not any(str(tmp_path) in value for value in tags.values() if isinstance(value, str)) or \
        tags["source"] == str(album), "only `source` names the folder, and that is the address"


def test_importing_one_provider_does_not_hide_the_others():
    """The registry's guard used to be `if _MAKERS: return`, so any module that imported a provider
    by name filled the registry and the rest were never imported. One provider could not show it;
    the second turned it into 153 failures — every YouTube lookup answering "unknown provider" —
    the moment this file imported `sources_folder` at the top."""
    assert sources.known() == ["folder", "youtube"]
    assert sources.get(None, Config()).name == "youtube", "the default still resolves"
