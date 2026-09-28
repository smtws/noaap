"""Offering an album to MusicBrainz (DESIGN.md §9, slice 43).

Nothing here talks to musicbrainz.org, and nothing in this feature ever does: seeding is a form
that opens in the user's own browser, signed in as them, and noaap holds no credentials. So what
is testable is exactly what noaap decides — which albums to offer, and what the boxes say.
"""

import threading

import httpx
import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap.config import Config
from noaap.download import run, save_plan
from noaap.mb import length_disagreement, recording_edit_url, seed_release, seed_url, seedable
from noaap.models import AlbumPlan, PlanTrack
from noaap.plan import build_plan
from noaap.service import Service
from noaap.web import App


@pytest.fixture
def library(tmp_path, opus_template):
    plan = build_plan(vol1())
    run(plan, tmp_path / plan.folder, FakeYouTube(opus_template))
    return tmp_path


@pytest.fixture
def server(library, opus_template):
    yt = FakeYouTube(opus_template)
    app = App(Config(musicbrainz=False), library, port=0,
              service_factory=lambda job: Service(Config(musicbrainz=False), library, log=job.log.append, yt=yt))
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as client:
        yield app, client
    srv.shutdown()


def a_track(number: int, title: str, **fields) -> PlanTrack:
    track = PlanTrack(video_id=f"v{number}", number=number, artist="Feuerschwanz", title=title,
                      filename=f"{number:02d}.opus", provenance={}, state="done")
    for name, value in fields.items():
        setattr(track, name, value)
    return track


def a_plan(**fields) -> AlbumPlan:
    plan = AlbumPlan(source_url="https://www.youtube.com/playlist?list=PL1", source_id="PL1",
                     kind="official_album", album="Fegefeuer", albumartist="Feuerschwanz", year=2023,
                     cover_url=None, folder="Feuerschwanz/Fegefeuer",
                     tracks=[a_track(1, "SGFRD Dragonslayer", file_length=252.4),
                             a_track(2, "Bastard von Asgard", file_length=234.8, artist="Feuerschwanz feat. Fabienne Erni")])
    for name, value in fields.items():
        setattr(plan, name, value)
    return plan


# -- who is offered -----------------------------------------------------------------------------


def test_an_unmatched_album_with_files_may_be_offered():
    assert seedable(a_plan()) == ""


def test_an_album_musicbrainz_already_has_is_not_offered():
    assert "already has this release" in seedable(a_plan(mbid="a-release-id"))


def test_compilations_and_playlists_are_never_offered():
    # MusicBrainz wants releases that were released; a folder of favourites is not one
    assert "not compilations" in seedable(a_plan(kind="compilation"))
    assert "playlist somebody made" in seedable(a_plan(kind="artist_playlist"))


def test_an_album_with_nothing_downloaded_or_no_name_is_not_offered():
    empty = a_plan()
    for track in empty.tracks:
        track.state = "pending"
    assert "nothing has been downloaded" in seedable(empty)
    assert "a title and an artist" in seedable(a_plan(album="  "))
    assert "a title and an artist" in seedable(a_plan(albumartist=""))


# -- what the form says -------------------------------------------------------------------------


def test_the_seed_fills_their_documented_fields():
    got = seed_release(a_plan())
    assert got["name"] == "Fegefeuer"
    assert got["artist_credit.names.0.name"] == "Feuerschwanz"
    assert got["artist_credit.names.0.artist.name"] == "Feuerschwanz"
    assert got["type"] == "Album" and got["mediums.0.format"] == "Digital Media"
    assert got["events.0.date.year"] == "2023"
    assert got["urls.0.url"] == "https://www.youtube.com/playlist?list=PL1"
    assert "noaap" in got["edit_note"] and "check everything" in got["edit_note"]


def test_the_tracklist_carries_the_lengths_measured_from_the_files():
    got = seed_release(a_plan())
    assert got["mediums.0.track.0.name"] == "SGFRD Dragonslayer"
    assert got["mediums.0.track.0.number"] == "1"
    assert got["mediums.0.track.0.length"] == "252400"      # milliseconds, as their format wants
    assert got["mediums.0.track.1.length"] == "234800"


def test_a_track_artist_is_seeded_only_where_it_differs_from_the_albums():
    got = seed_release(a_plan())
    assert "mediums.0.track.0.artist_credit.names.0.name" not in got   # the album's own artist
    assert got["mediums.0.track.1.artist_credit.names.0.name"] == "Feuerschwanz feat. Fabienne Erni"


def test_tracks_that_are_not_there_are_not_offered_as_tracks():
    plan = a_plan()
    plan.tracks[0].state = "failed"
    got = seed_release(plan)
    assert got["mediums.0.track.0.name"] == "Bastard von Asgard"       # the one that exists
    assert "mediums.0.track.1.name" not in got


def test_a_single_is_seeded_as_a_single():
    assert seed_release(a_plan(kind="single"))["type"] == "Single"


def test_the_seed_never_carries_a_path_or_anything_of_ours():
    got = seed_release(a_plan())
    assert not any("/home/" in v or ".opus" in v for v in got.values())


# -- the one thing seeding cannot do -------------------------------------------------------------


def test_a_recording_whose_length_disagrees_is_named_with_both_numbers():
    track = a_track(1, "Song", mbid="rec-1", file_length=252.4, mb_length=211.0)
    got = length_disagreement(track)
    assert got == {"ours": 252.4, "theirs": 211.0, "apart": 41.4}


def test_small_differences_are_not_worth_anybodys_time():
    assert length_disagreement(a_track(1, "Song", mbid="rec-1", file_length=252.4, mb_length=250.0)) is None
    # and a track MusicBrainz does not know cannot be corrected on MusicBrainz
    assert length_disagreement(a_track(1, "Song", file_length=252.4, mb_length=211.0)) is None
    assert length_disagreement(a_track(1, "Song", mbid="rec-1", file_length=252.4)) is None


def test_the_links_are_their_pages_and_can_be_pointed_elsewhere(monkeypatch):
    assert seed_url().endswith("/release/add")
    assert recording_edit_url("rec-1").endswith("/recording/rec-1/edit")
    # the override exists so a test can open a form that is not theirs (catalog AD)
    monkeypatch.setattr("noaap.mb.WEB", "http://127.0.0.1:8796")
    assert seed_url() == "http://127.0.0.1:8796/release/add"


# -- through the web layer ------------------------------------------------------------------------


def test_the_endpoint_hands_the_page_a_form_and_refuses_what_it_should(server):
    """The page never decides this alone: a button is a suggestion, the server is the gate."""
    app, client = server
    album_id = app.albums()[0]["id"]
    # the fixture's album is a compilation, which is exactly what is *not* offered
    refused_first = client.get(f"/api/mbseed?id={album_id}")
    assert refused_first.status_code == 400 and "not compilations" in refused_first.json()["error"]

    album_dir, plan = app.album(album_id)
    plan.kind = "official_album"
    save_plan(plan, album_dir)
    got = client.get(f"/api/mbseed?id={album_id}").json()
    assert got["url"].endswith("/release/add")
    assert got["fields"]["name"] and got["fields"]["mediums.0.format"] == "Digital Media"
    assert got["fields"]["mediums.0.track.0.length"].isdigit()

    # and an album nobody has heard of is a 404, not a form
    assert client.get("/api/mbseed?id=nope").status_code == 404


def test_the_page_is_told_where_musicbrainz_lives(server):
    app, client = server
    assert client.get("/api/state").json()["settings"]["musicbrainz_web"].startswith("http")
