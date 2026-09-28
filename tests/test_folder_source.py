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

# long enough to be a song: anything under `plan.MIN_TRACK_SECONDS` is read as an intro card and
# never becomes a track, which is right for a playlist and would silently empty a folder here
SECONDS = 31


def encode(path: Path, **tags: str) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    meta = [arg for key, value in tags.items() for arg in ("-metadata", f"{key}={value}")]
    codec = {".flac": "flac", ".mp3": "libmp3lame", ".opus": "libopus"}[path.suffix]
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=duration={SECONDS}",
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
    assert all(e.duration == pytest.approx(SECONDS, abs=0.5) for e in found.entries)
    assert found.thumbnail == str(album / "cover.jpg")


def test_the_track_number_orders_it_not_the_file_name(tmp_path, source):
    folder = tmp_path / "album"
    for name, number, title in (("zz.flac", "1", "First"), ("aa.flac", "2", "Second")):
        encode(folder / name, title=title, artist="A", album="B", track=number)

    assert [e.title for e in source.collection(str(folder)).entries] == ["First", "Second"]


def test_without_a_track_number_tag_the_name_supplies_one(tmp_path, source):
    """The name is read per field and only where a tag is absent, so these files gain both a
    number and a title from it — which is also what orders them."""
    folder = tmp_path / "album"
    for name in ("02 - second.flac", "01 - first.flac"):
        encode(folder / name, artist="A", album="B")

    entries = source.collection(str(folder)).entries
    assert [e.title for e in entries] == ["first", "second"]
    assert [e.music.artist for e in entries] == ["A", "A"], "and the tag still wins where there is one"


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
    assert entry.duration == pytest.approx(SECONDS, abs=0.5)


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
    assert broken.title == "broken", "no tags to read, so the name is read instead"
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


# -- how a folder spells more than one disc --------------------------------------------------------


def discs(folder: Path, *names: str) -> Path:
    for disc, name in enumerate(names, 1):
        for n in (1, 2):
            encode(folder / name / f"{n:02d} - t{disc}{n}.flac", title=f"t{disc}{n}",
                   artist="In Extremo", album="Am goldenen Rhein", track=str(n))
    return folder


@pytest.mark.parametrize("names", [("cd1", "cd2"), ("CD 1", "CD 2"), ("1", "2"), ("Disc 1", "Disc 2")])
def test_numbered_sub_folders_are_the_discs_of_one_album(tmp_path, source, names):
    """Three spellings occur in the reference collection, in three different albums."""
    folder = discs(tmp_path / "Am goldenen Rhein", *names)

    found = source.collection(str(folder))

    assert len(found.entries) == 4
    assert [e.disc for e in found.entries] == [1, 1, 2, 2]
    assert found.title == "Am goldenen Rhein"


def test_each_disc_is_numbered_from_one(tmp_path, source):
    from noaap.plan import build_plan

    plan = build_plan(source.collection(str(discs(tmp_path / "album", "cd1", "cd2"))), source=source)

    assert [(t.disc, t.number) for t in plan.tracks] == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_one_numbered_folder_among_named_ones_is_not_a_disc_split(tmp_path, source):
    """All of them or none. A bonus disc beside three named folders is not this shape, and
    guessing which of them were discs would be worse than reading the folder flat."""
    folder = tmp_path / "album"
    encode(folder / "cd1" / "01 - a.flac", title="a", artist="A", album="B", track="1")
    encode(folder / "bonus tracks" / "01 - b.flac", title="b", artist="A", album="B", track="1")

    with pytest.raises(sources.NotSupported, match="no audio"):
        source.collection(str(folder))


def test_sibling_folders_are_one_album_when_their_tags_agree(tmp_path, source):
    for disc in (1, 2):
        for n in (1, 2):
            encode(tmp_path / f"The Better Life Disc {disc}" / f"{n:02d} - t{disc}{n}.flac",
                   title=f"t{disc}{n}", artist="3 Doors Down", album="The Better Life", track=str(n))

    found = source.collection(str(tmp_path / "The Better Life Disc 2"))

    assert [e.disc for e in found.entries] == [1, 1, 2, 2], "anchored on the lowest, from either side"
    assert found.source_url.endswith("Disc 1")


def test_siblings_whose_own_tags_name_two_albums_stay_two_albums(tmp_path, source):
    """The one real case in the reference collection: `The Better Life [Deluxe Edition] Disc 1|2`,
    whose files say the album *is* "… Disc 1" and "… Disc 2". This is the grouping the filesystem
    did not state, so the tags get the vote, and here they vote against it."""
    for disc in (1, 2):
        encode(tmp_path / f"The Better Life Disc {disc}" / "01 - a.flac",
               title="a", artist="3 Doors Down", album=f"The Better Life Disc {disc}", track="1")

    found = source.collection(str(tmp_path / "The Better Life Disc 1"))

    assert len(found.entries) == 1 and found.title == "The Better Life Disc 1"


# -- what a name can say when the tags say nothing --------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("Feuerschwanz - Drachentanz (Live 2008) - 01 - Turnier (Live 2008)",
     {"artist": "Feuerschwanz", "album": "Drachentanz (Live 2008)", "n": 1, "title": "Turnier (Live 2008)"}),
    ("01 - Fleetwood Mac - Rhiannon", {"artist": "Fleetwood Mac", "n": 1, "title": "Rhiannon"}),
    ("03 - Trust in Rust", {"n": 3, "title": "Trust in Rust"}),
    ("07. Melody", {"n": 7, "title": "Melody"}),
    ("just a name", {}),
])
def test_a_file_name_is_read_as_the_record_it_is(name, expected):
    from noaap.sources_folder import from_name

    found = from_name(Path(f"{name}.opus"))

    assert found.get("title") == expected.get("title")
    assert found.get("artist") == expected.get("artist")
    assert found.get("album") == expected.get("album")
    assert found.get("tracknumber") == expected.get("n")


def test_an_untagged_album_is_recovered_and_says_where_from(tmp_path, source):
    """The 17 untagged files in the reference collection are one album, named by this program's
    own output scheme. So the recovery is exact — and the plan records that it was the name."""
    from noaap.models import Provenance
    from noaap.plan import build_plan

    folder = tmp_path / "Drachentanz (Live 2008)"
    titles = ["Drachentanz (Live 2008)", "Turnier (Live 2008)", "Der Barbier (Live 2008)",
              "Das Groupie (Live 2008)", "Der Glöckner (Live 2008)"]
    for n, title in enumerate(titles, 1):
        encode(folder / f"Feuerschwanz - Drachentanz (Live 2008) - {n:02d} - {title}.opus")

    plan = build_plan(source.collection(str(folder)), source=source)

    assert plan.albumartist == "Feuerschwanz" and plan.album == "Drachentanz (Live 2008)"
    assert plan.provenance["albumartist"] == Provenance.FILE_NAME
    assert [t.title for t in plan.tracks] == ["Drachentanz (Live 2008)", "Turnier", "Der Barbier",
                                              "Das Groupie", "Der Glöckner"], \
        "the album's name comes out of the titles — except the one that *is* it"
    assert all(t.provenance["title"] == Provenance.FILE_NAME for t in plan.tracks)


def test_a_tag_always_beats_the_name(tmp_path, source):
    from noaap.models import Provenance
    from noaap.plan import build_plan

    folder = tmp_path / "album"
    encode(folder / "Someone - Some Album - 01 - Wrong.flac", title="Right", artist="Right Artist",
           album="Right Album", track="1")

    plan = build_plan(source.collection(str(folder)), source=source)

    assert plan.tracks[0].title == "Right" and plan.tracks[0].artist == "Right Artist"
    assert plan.tracks[0].provenance["title"] == Provenance.FILE_TAGS


# -- covers, and what is left alone ------------------------------------------------------------------


def test_a_folder_without_a_cover_file_falls_back_to_the_picture_inside_a_track(tmp_path, source):
    """65 of 135 folders in the reference collection have a cover file; 269 of 2000 files carry a
    picture. The second number is why this fallback exists."""
    from noaap.models import AlbumPlan, Kind, PlanTrack
    from noaap.tag import tag_file

    folder = tmp_path / "album"
    path = encode(folder / "01 - a.flac", title="a", artist="A", album="B", track="1")
    plan = AlbumPlan(source_url=str(folder), source_id=str(folder), kind=Kind.OFFICIAL_ALBUM,
                     album="B", albumartist="A", year=None, cover_url=None, folder="x",
                     tracks=[PlanTrack(video_id=str(path), number=1, artist="A", title="a",
                                       filename="a.flac", provenance={})], provider="folder")
    tag_file(path, plan, plan.tracks[0], cover=JPEG)

    found = source.collection(str(folder))

    assert found.thumbnail is None, "there is no cover file to name"
    assert source.art(str(folder)) == JPEG, "so the folder itself is the address for the one inside"


def test_what_is_left_alone_is_counted_by_kind(album, source):
    (album / "New Album Releases.url").write_text("[InternetShortcut]\n")
    (album / ".thumb").mkdir()
    (album / ".thumb" / "cover.jpg.jpg").write_bytes(b"thumbnail")

    left = source.ignored(str(album))

    assert left["url"] == 1
    assert left["image"] == 1, "the cover itself"
    assert left["hidden"] == 1
    assert (album / "New Album Releases.url").exists(), "counted, never touched"


def test_a_various_artists_folder_is_a_compilation(tmp_path, source):
    from noaap.models import Kind
    from noaap.plan import build_plan

    folder = tmp_path / "Classic Rock Hits"
    for n, (artist, title) in enumerate([("Fleetwood Mac", "Rhiannon"), ("ZZ Top", "La Grange"),
                                         ("Whitesnake", "Here I Go Again")], 1):
        encode(folder / f"{n:02d} - {artist} - {title}.mp3", title=title, artist=artist,
               album="Classic Rock Hits", album_artist="Various Artists", track=str(n))

    plan = build_plan(source.collection(str(folder)), source=source)

    assert plan.kind == Kind.COMPILATION
    assert plan.albumartist == "Various Artists"
    assert [t.artist for t in plan.tracks] == ["Fleetwood Mac", "ZZ Top", "Whitesnake"]


# -- one recording, more than one copy ---------------------------------------------------------------


def test_the_same_track_twice_is_two_candidates_not_two_tracks(tmp_path, source):
    """What P48's candidates were shaped for, arriving from a single source (§9, slice 50).

    **The reference collection does not hold this**, which is worth stating: its two mixed-format
    folders are one album each with a single track filled in from elsewhere (10 flac + 1 mp3,
    14 flac + 1 mp3), not an album kept twice. So this shape is real, tested, and so far unmet —
    the same standing as the sibling-disc merge.
    """
    from noaap.plan import build_plan

    folder = tmp_path / "Voices Of Doom"
    for suffix in (".flac", ".mp3"):
        encode(folder / f"02 - Gothic Queen{suffix}", title="Gothic Queen", artist="Mono Inc",
               album="Voices Of Doom", track="2")

    plan = build_plan(source.collection(str(folder)), source=source)

    assert len(plan.tracks) == 1
    track = plan.tracks[0]
    assert [c.codec for c in track.candidates] == ["flac", "mp3"], "lossless first"
    assert track.chosen == track.candidates[0].ref and track.chosen.endswith(".flac")
    assert all(c.provider == "folder" for c in track.candidates)
    assert all(c.why == "default, not ranked" for c in track.candidates), "P52 can find every one"


def test_a_copy_carries_what_ranking_will_want(tmp_path, source):
    folder = tmp_path / "album"
    path = encode(folder / "01 - a.flac", title="a", artist="A", album="B", track="1")

    candidate = source.collection(str(folder)).entries[0].copies[0]

    assert candidate.bytes == path.stat().st_size
    assert candidate.codec == "flac" and candidate.channels == 1
    assert candidate.length == pytest.approx(SECONDS, abs=0.5)
    assert candidate.stream_sha and len(candidate.stream_sha) == 32


def test_the_digest_is_of_the_audio_and_not_of_the_file(tmp_path, source):
    """A file hash finds nothing in the reference collection — every copy differs in its tags.
    This is what tells the same recording from another encoding of it."""
    from noaap.models import AlbumPlan, Kind, PlanTrack
    from noaap.sources_folder import stream_sha
    from noaap.tag import tag_file

    path = encode(tmp_path / "album" / "01 - a.flac", title="a", artist="A", album="B", track="1")
    before, bytes_before = stream_sha(path), path.read_bytes()

    plan = AlbumPlan(source_url="x", source_id="x", kind=Kind.OFFICIAL_ALBUM, album="Renamed",
                     albumartist="Someone Else", year=2001, cover_url=None, folder="x",
                     tracks=[PlanTrack(video_id=str(path), number=1, artist="Someone Else",
                                       title="Renamed", filename="a.flac", provenance={})],
                     provider="folder")
    tag_file(path, plan, plan.tracks[0], cover=JPEG)

    assert path.read_bytes() != bytes_before, "the file did change"
    assert stream_sha(path) == before, "and the recording did not"


def test_two_encodings_of_one_recording_are_not_the_same_stream(tmp_path, source):
    from noaap.sources_folder import stream_sha

    flac = encode(tmp_path / "a.flac", title="a")
    mp3 = encode(tmp_path / "a.mp3", title="a")

    assert stream_sha(flac) != stream_sha(mp3), "the same music, not the same recording"


# -- what the CLI and the page are given --------------------------------------------------------------


def test_one_guest_credit_does_not_turn_an_album_into_a_compilation(tmp_path, source):
    """A folder's `artist` tags name guests — "van Canto, Kai Hansen" is one tag among twenty — and
    counting distinct artists then says two. `albumartist` is what a folder has instead of a guess."""
    from noaap.models import Kind
    from noaap.plan import build_plan

    folder = tmp_path / "Trust In Rust"
    for n, artist in enumerate(["Van Canto", "Van Canto", "Van Canto, Kai Hansen"], 1):
        encode(folder / f"{n:02d} - t{n}.flac", title=f"t{n}", artist=artist,
               album="Trust in Rust", album_artist="Van Canto", track=str(n))

    plan = build_plan(source.collection(str(folder)), source=source)

    assert plan.kind == Kind.OFFICIAL_ALBUM
    assert plan.albumartist == "Van Canto"


def test_various_artists_is_the_one_album_artist_that_means_the_opposite(tmp_path, source):
    from noaap.models import Kind
    from noaap.plan import build_plan

    folder = tmp_path / "Classic Rock Hits"
    for n, artist in enumerate(["Fleetwood Mac", "ZZ Top", "Whitesnake"], 1):
        encode(folder / f"{n:02d} - t{n}.mp3", title=f"t{n}", artist=artist,
               album="Classic Rock Hits", album_artist="Various Artists", track=str(n))

    plan = build_plan(source.collection(str(folder)), source=source)

    assert plan.kind == Kind.COMPILATION


def test_a_fetch_finds_the_provider_by_the_address(tmp_path, album):
    """`noaap fetch <a path>` has to reach the folder provider without being told."""
    from noaap.config import Config as Cfg
    from noaap.service import Service

    service = Service(Cfg(musicbrainz=False, lyrics=False), tmp_path / "library", log=lambda s: None)

    assert service.source_for_address(str(album)).name == "folder"
    assert service.source_for_address("https://www.youtube.com/playlist?list=PLx").name == "youtube"


def test_an_album_already_in_the_library_is_reported_and_left_alone(tmp_path, album):
    """The amendment to decision 3: matching a file to a track we already hold is the
    same-recording question, and that is P52's first rule."""
    from noaap.config import Config as Cfg
    from noaap.download import save_plan
    from noaap.models import AlbumPlan, Kind, PlanTrack
    from noaap.service import Service

    library = tmp_path / "library"
    existing = AlbumPlan(source_url="https://www.youtube.com/playlist?list=PLx", source_id="PLx",
                         kind=Kind.OFFICIAL_ALBUM, album="Trust in Rust", albumartist="Van Canto",
                         year=2018, cover_url=None, folder="Van Canto/Trust in Rust",
                         tracks=[PlanTrack(video_id="vid1", number=1, artist="Van Canto",
                                           title="Back in the Lead", filename="a.opus", provenance={})])
    save_plan(existing, library / existing.folder)
    service = Service(Cfg(musicbrainz=False, lyrics=False), library, log=lambda s: None)

    outcome = service.fetch(str(album))

    assert outcome.status == "held"
    assert "already in the library" in outcome.message and "1 of 3 titles overlap" in outcome.message
    assert not (library / "Van Canto" / "Trust in Rust" / "01 - Back in the Lead.mp3").exists()


def test_two_songs_that_share_a_track_number_stay_two_songs(tmp_path, source):
    """Found by running the intake over the real collection: grouping by (disc, number) alone
    merged "Subway To Sally - Kleid Aus Rosen" with "Blutengel - Seelenschmerz", both tagged as
    track 10 of the same compilation, and the second **disappeared from the album**. A number has
    to agree with a title before two files are one recording."""
    folder = tmp_path / "Vol.17"
    encode(folder / "10 - Kleid Aus Rosen.flac", title="Kleid Aus Rosen", artist="Subway To Sally",
           album="Vol.17", track="10")
    encode(folder / "10 - Seelenschmerz.flac", title="Seelenschmerz", artist="Blutengel",
           album="Vol.17", track="10")

    found = source.collection(str(folder))

    assert len(found.entries) == 2, "a collision, not an identity"
    assert sorted(e.title for e in found.entries) == ["Kleid Aus Rosen", "Seelenschmerz"]
    assert all(len(e.copies) == 1 for e in found.entries)


def test_a_title_that_differs_by_a_letter_is_not_the_same_recording(tmp_path, source):
    """The other real one: `Looking Bach.mp3` at 320 kbps beside `Looking Back.mp3` at 128, both
    track 10. Probably one song and a typo — but probably is not a thing to merge audio on, and
    both digests are recorded for P52 to decide with."""
    folder = tmp_path / "Head Under Water"
    for title in ("Looking Bach", "Looking Back"):
        encode(folder / f"10 - {title}.mp3", title=title, artist="Mono Inc", album="x", track="10")

    assert len(source.collection(str(folder)).entries) == 2


# -- two rules that were the core's and are the provider's ------------------------------------------


def test_a_length_the_header_will_not_give_is_decoded(tmp_path, source, monkeypatch):
    """Three albums in the reference collection are FLACs whose header says `total_samples = 0`.
    Without a length they would have no length chip, no duration for LRCLIB, no near-miss check
    and no trim reference — so the provider reads the audio rather than shrugging."""
    from noaap import sources_folder

    path = encode(tmp_path / "album" / "01 - a.flac", title="a", artist="A", album="B", track="1")
    monkeypatch.setattr(sources_folder, "audio_length", lambda p: None)  # a header that will not say

    candidate = source.collection(str(tmp_path / "album")).entries[0].copies[0]

    assert candidate.length == pytest.approx(SECONDS, abs=0.5)
    assert candidate.length_by == "decoded", "and it says how it came by the number"
    assert sources_folder.decoded_length(path) == pytest.approx(SECONDS, abs=0.5)


def test_a_header_that_answers_is_not_decoded(tmp_path, source, monkeypatch):
    from noaap import sources_folder

    encode(tmp_path / "album" / "01 - a.flac", title="a", artist="A", album="B", track="1")
    monkeypatch.setattr(sources_folder, "decoded_length",
                        lambda p: pytest.fail("decoded a file whose header answered"))

    assert source.collection(str(tmp_path / "album")).entries[0].copies[0].length_by is None


def test_nothing_in_a_folder_is_too_short_to_be_a_track(tmp_path, source):
    """30 seconds is YouTube's rule about intro cards in a playlist. A short file in an album
    folder is an interlude, a skit or a spoken intro, and it belongs to the album."""
    from noaap.plan import build_plan, shortest_track

    folder = tmp_path / "album"
    encode(folder / "01 - intro.flac", title="Intro", artist="A", album="B", track="1")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=4",
                    "-c:a", "flac", str(folder / "02 - skit.flac"), "-metadata", "title=Skit"], check=True)

    assert shortest_track(source) == 0.0
    assert shortest_track() == 30, "and YouTube's rule is untouched"

    plan = build_plan(source.collection(str(folder)), source=source)

    assert len(plan.tracks) == 2 and plan.skipped == []


def test_a_multi_disc_album_is_one_entry_in_a_listing(tmp_path, source):
    """Found by reconciling files on disk against tracks planned: 67 files — four albums — were
    in no album at all. A disc-parent holds no audio of its own, so the listing walked past it
    and the whole album was invisible from the collection root."""
    artist = tmp_path / "In Extremo"
    discs(artist / "Am goldenen Rhein", "cd1", "cd2")
    encode(artist / "Sterneneisen" / "01 - a.flac", title="a", artist="In Extremo",
           album="Sterneneisen", track="1")

    found = source.listing(str(tmp_path))

    assert sorted(r.title for r in found) == ["Am goldenen Rhein", "Sterneneisen"]
    assert next(r.count for r in found if r.title == "Am goldenen Rhein") == 4
    assert len(source.collection(next(r.url for r in found if r.title == "Am goldenen Rhein")).entries) == 4
