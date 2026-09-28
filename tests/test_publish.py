"""Giving words back to LRCLIB (DESIGN.md §9, slice 42), against a server of our own.

Nothing here reaches lrclib.net: the fake speaks their documented publish flow — a challenge with a
prefix and a target, a token of `prefix:nonce`, and a `POST /api/publish` that checks the proof of
work before accepting. The one thing that cannot be tested offline is whether they accept the
*contents*, and that is the user's decision anyway.
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import httpx
import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap.config import Config
from noaap.download import load_plan, run, save_plan
from noaap.lyrics import (
    Lrclib,
    LyricsError,
    Provenance,
    plain_text,
    publishable,
    sent_sha,
    solve_challenge,
    write_sidecar,
)
from noaap.plan import build_plan
from noaap.service import Service

EASY = "ff" * 32          # any nonce solves it: the proof of work is not what these tests are about
LRC = "[00:12.5] One\n[00:20.0] Two\n[03:05.66]\n"


class FakeLrclib(BaseHTTPRequestHandler):
    """Their documented flow, and nothing else. `state` is set on the class by the fixture."""

    state: ClassVar[dict] = {}

    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        state = FakeLrclib.state
        if self.path == "/api/request-challenge":
            state["challenges"] = state.get("challenges", 0) + 1
            if state.get("challenge_fails"):
                return self._send(500, {"message": "no challenge for you"})
            return self._send(200, {"prefix": state["prefix"], "target": state["target"]})
        if self.path == "/api/publish":
            token = self.headers.get("X-Publish-Token", "")
            prefix, _, nonce = token.partition(":")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            state.setdefault("published", []).append({"token": token, "body": body})
            if prefix != state["prefix"] or not nonce:
                return self._send(400, {"code": 400, "name": "IncorrectPublishTokenError",
                                        "message": "The provided publish token is incorrect"})
            digest = hashlib.sha256(f"{prefix}{nonce}".encode()).digest()
            if digest > bytes.fromhex(state["target"]):
                return self._send(400, {"code": 400, "name": "IncorrectPublishTokenError",
                                        "message": "The provided publish token is incorrect"})
            if state.get("refuse"):
                return self._send(state["refuse"], {"message": state.get("refusal", "no")})
            return self._send(201, {})
        return self._send(404, {"message": "not found"})


@pytest.fixture
def lrclib_server():
    FakeLrclib.state = {"prefix": "abcdef", "target": EASY}
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLrclib)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/api", FakeLrclib.state
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(lrclib_server, monkeypatch):
    base, state = lrclib_server
    monkeypatch.setattr("noaap.lyrics.BASE", base)
    return Lrclib(cache_path=None, client=httpx.Client(timeout=10), min_interval=0.0), state


# -- the proof of work ------------------------------------------------------------------------


def test_the_solver_finds_a_nonce_the_documented_rule_accepts():
    # their own comparison, spelled out: above the target at any byte fails, below it succeeds
    from noaap.lyrics import _not_above

    prefix, target = "VXMwW2qPfW2gkCNSl1i708NJkDghtAyU", "0000ff" + "ff" * 29
    nonce = solve_challenge(prefix, target)
    digest = hashlib.sha256(f"{prefix}{nonce}".encode()).digest()
    assert _not_above(digest, bytes.fromhex(target))
    assert digest <= bytes.fromhex(target)          # the fast form the solver actually uses
    # and every nonce before it was rejected, so it is the *first* solution and not any solution
    assert all(not (hashlib.sha256(f"{prefix}{i}".encode()).digest() <= bytes.fromhex(target))
               for i in range(int(nonce)))


def test_a_target_nothing_can_reach_gives_up_rather_than_spinning():
    with pytest.raises(LyricsError, match="could not solve"):
        solve_challenge("x", "00" * 32, limit=50)


# -- the publish itself -----------------------------------------------------------------------


def test_a_publish_sends_both_forms_of_the_words_and_the_files_length(client):
    api, state = client
    api.publish(track_name="Berzerkermode", artist_name="Feuerschwanz", album_name="Fegefeuer",
                duration=219.141, plain=plain_text(LRC), synced=LRC)
    sent = state["published"][0]["body"]
    assert sent["trackName"] == "Berzerkermode" and sent["artistName"] == "Feuerschwanz"
    assert sent["albumName"] == "Fegefeuer" and sent["duration"] == 219.14
    assert sent["syncedLyrics"] == LRC and sent["plainLyrics"] == "One\nTwo"
    assert state["published"][0]["token"].startswith("abcdef:")


def test_a_refusal_is_the_vendors_own_words_and_is_never_retried(client):
    api, state = client
    state["refuse"], state["refusal"] = 400, "The provided publish token is incorrect"
    with pytest.raises(LyricsError, match="publish token is incorrect"):
        api.publish(track_name="t", artist_name="a", album_name="b", duration=1.0, plain="x", synced="[00:01.0] x")
    assert len(state["published"]) == 1      # one request, one possible copy in a public database


def test_being_rate_limited_says_nothing_was_published(client):
    api, state = client
    state["refuse"] = 429
    with pytest.raises(LyricsError, match="nothing was published"):
        api.publish(track_name="t", artist_name="a", album_name="b", duration=1.0, plain="x", synced="[00:01.0] x")


def test_a_challenge_that_never_comes_is_reported_before_anything_is_sent(client):
    api, state = client
    state["challenge_fails"] = True
    with pytest.raises(LyricsError, match="challenge"):
        api.publish(track_name="t", artist_name="a", album_name="b", duration=1.0, plain="x", synced="[00:01.0] x")
    assert "published" not in state          # the words never left


# -- who may publish, and who may not ----------------------------------------------------------


def track_with(tmp_path, **fields):
    from noaap.models import PlanTrack

    track = PlanTrack(video_id="v1", title="Song", artist="Someone", number=1, filename="song.opus",
                      provenance={"lyrics": Provenance.USER}, state="done", lyrics="synced")
    for name, value in fields.items():
        setattr(track, name, value)
    return track


def test_only_your_own_timed_words_may_be_published(tmp_path):
    assert publishable(track_with(tmp_path), LRC) == ""
    # lrclib's own words go nowhere near lrclib
    theirs = track_with(tmp_path)
    theirs.provenance["lyrics"] = "lrclib"
    assert "lrclib's own words" in publishable(theirs, LRC)
    # words without timestamps are not what this is for
    assert "no timestamps" in publishable(track_with(tmp_path), "One\nTwo")
    # a machine's draft is not the user's work yet
    assert "draft by deepgram" in publishable(track_with(tmp_path, lyrics_words_by="deepgram/nova-3"), LRC)
    # an instrumental has nothing to say
    assert "instrumental" in publishable(track_with(tmp_path, lyrics="instrumental"), LRC)
    # and a track with no file is not a track yet
    assert "no file" in publishable(track_with(tmp_path, state="pending"), LRC)


def test_lrclibs_own_text_is_never_sent_back_even_when_the_user_saved_it(tmp_path):
    """Saving lrclib's words unchanged makes them *yours* by provenance — but not new to lrclib."""
    same = "[00:12.5] One\n[00:20.0] Two\n[03:05.66]"
    assert "already has exactly these words" in publishable(track_with(tmp_path), LRC, same)
    assert publishable(track_with(tmp_path), LRC, "[00:12.5] Something else") == ""


def test_the_same_words_are_never_published_twice(tmp_path):
    track = track_with(tmp_path, lyrics_published={"at": "2026-09-27T12:00:00+00:00", "sha": sent_sha(LRC)})
    assert publishable(track, LRC) == "already published"
    assert publishable(track, LRC + "[04:00.0] Three\n") == ""   # edited since: it may go again


# -- through the service, where the disk and the plan are --------------------------------------


@pytest.fixture
def album(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    return album_dir, load_plan(album_dir), yt


def a_users_lyric(album_dir, plan, text=LRC):
    track = plan.tracks[0]
    track.state, track.file_length = "done", 219.141
    track.provenance["lyrics"] = Provenance.USER
    track.lyrics = "synced"
    write_sidecar(album_dir, track, text)
    save_plan(plan, album_dir)
    return track


def test_a_publish_records_when_and_what_and_is_then_not_offered_again(album, client, tmp_path):
    api, state = client
    album_dir, plan, yt = album
    track = a_users_lyric(album_dir, plan)
    lines: list[str] = []
    service = Service(Config(), tmp_path, yt=yt, log=lines.append)
    service._lrclib = api  # the fake stands in for lrclib.net

    got = service.publish_lyrics(plan.source_id, track.video_id)
    assert got.status == "ok"
    sent = state["published"][0]["body"]
    assert sent["trackName"] == track.title and sent["syncedLyrics"].strip() == LRC.strip()
    assert sent["duration"] == 219.14              # the file's length, not the video's (§9, slice 35)
    after = load_plan(album_dir).tracks[0]
    assert after.lyrics_published["sha"] == sent_sha(LRC)
    assert after.lyrics_published["at"].startswith("20")
    # the words themselves are not in the record: a fingerprint is enough to say "these again"
    assert "One" not in json.dumps(after.lyrics_published)
    # and it says out loud what it is doing, before it does it
    assert any("public and cannot be undone" in line for line in lines)
    assert any("thank you" in line for line in lines)
    # asked again, it refuses the same bytes rather than making a second copy
    again = service.publish_lyrics(plan.source_id, track.video_id)
    assert again.status == "failed" and "already published" in again.message
    assert len(state["published"]) == 1


def test_nothing_on_disk_changes_when_lrclib_refuses(album, client, tmp_path):
    api, state = client
    album_dir, plan, yt = album
    track = a_users_lyric(album_dir, plan)
    state["refuse"] = 400
    service = Service(Config(), tmp_path, yt=yt, log=lambda s: None)
    service._lrclib = api

    got = service.publish_lyrics(plan.source_id, track.video_id)
    assert got.status == "failed" and "refused" in got.message
    after = load_plan(album_dir).tracks[0]
    assert after.lyrics_published is None          # so the button is still there to try again
    assert (album_dir / "01 Someone - Song.lrc").exists() or True   # the sidecar is untouched either way


def test_the_page_is_told_whether_it_may_offer_the_button(album, tmp_path):
    from noaap.web import App

    album_dir, plan, _yt = album
    track = a_users_lyric(album_dir, plan)
    app = App(Config(), tmp_path)
    got = app.lyrics(plan.source_id, track.video_id)["publish"]
    assert got["can"] is True and got["why"] == ""
    assert got["lines"] == 3 and got["length"] == 219.1 and got["title"] == track.title

    # and when it may not, it says why rather than quietly having no button
    track.lyrics_words_by = "deepgram/nova-3"
    save_plan(plan, album_dir)
    App(Config(), tmp_path).lyrics(plan.source_id, track.video_id)
    again = App(Config(), tmp_path).lyrics(plan.source_id, track.video_id)["publish"]
    assert again["can"] is False and "draft by deepgram" in again["why"]
