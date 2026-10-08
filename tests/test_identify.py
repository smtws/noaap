"""“Identify with MusicBrainz”, for one album (DESIGN §9, slice 142; R-506 item 2).

The user pinned a release by hand and nothing happened until a pass was run from a terminal: the
panel had no way to say *go and look this album up*. It is the same two steps as a repair — look
first, apply what was listed — because the lookup rewrites titles, numbers and discs, and a person
who cannot see that first is being asked to trust it blind.

The albums here are **adopted from a folder**, which is what the user's library is: 1,312 of their
1,523 albums. A folder read calls an album `official_album`, which is the shape a release is matched
against at all.
"""

from __future__ import annotations

import time

import pytest
from test_intake import QUIET
from test_repointing import a_library, an_album

from noaap import intake
from noaap.config import Config
from noaap.download import load_plan, save_plan
from noaap.service import Service
from noaap.web import App

HDR = {"X-Noaap": "1", "Content-Type": "application/json"}
RELEASE = "73fcbc7e-4945-4b33-bdc0-671a0aeffdc4"
TITLES = ["Path Vol. II", "Struggle", "Romance", "Pray"]


class SaysOneRelease:
    """A MusicBrainz that answers with one release, and counts what it was asked."""

    def __init__(self, title="Cult", titles=None):
        self.title, self.titles = title, titles or TITLES
        self.searched, self.opened = 0, []

    def _body(self, mbid):
        credit = [{"name": "Apocalyptica", "artist": {"name": "Apocalyptica"}}]
        return {"id": mbid, "title": self.title, "date": "2001-11-06", "artist-credit": credit,
                "release-group": {"id": "rg-1", "first-release-date": "2001-01-01"},
                "media": [{"position": 1, "tracks": [
                    {"position": n, "title": t, "artist-credit": credit,
                     "recording": {"id": f"rec-{n}"}}
                    for n, t in enumerate(self.titles, 1)]}]}

    def search_releases(self, artist, album):
        self.searched += 1
        return [{"id": RELEASE, "title": self.title, "score": 100, "status": "Official",
                 "country": "XW", "date": "2001-11-06", "track-count": len(self.titles),
                 "artist-credit": [{"name": "Apocalyptica", "artist": {"name": "Apocalyptica"}}]}]

    def release(self, mbid):
        self.opened.append(mbid)
        return self._body(mbid)

    def search_recordings(self, artist, title):
        return []

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


@pytest.fixture
def collection(tmp_path, one_second_of_sound):
    """One adopted album, as a folder take-in leaves it."""
    root = tmp_path / "collection"
    an_album(root / "Apocalyptica" / "Cult", TITLES, one_second_of_sound,
             artist="Apocalyptica", album="Cult")
    intake.take_in(a_library(root), root, QUIET, dry_run=False, log=lambda s: None)
    return root


def an_app(collection, mb=None):
    """The app with a fake MusicBrainz: a real request would be both slow and somebody else's."""
    cfg = Config(library_root=collection, musicbrainz=mb is not None, lyrics=False,
                 retag_adopted=True, rename_adopted=True)
    return App(cfg, collection, port=0,
               service_factory=lambda job: Service(cfg, collection, log=job.log.append, mb=mb,
                                                   cancel=job.cancel))


def settled(app, job):
    for _ in range(1500):
        if job.state not in ("queued", "running"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job never settled: {job.state}")


def the_album(app):
    return app.state()["albums"][0]["id"]


# -- the two steps -------------------------------------------------------------------------------


def test_the_look_writes_nothing_and_says_what_it_would_do(collection):
    mb = SaysOneRelease()
    app = an_app(collection, mb)
    source_id = the_album(app)
    before = load_plan(app.album(source_id)[0]).to_dict()

    job = settled(app, app.submit("identify", {"id": source_id, "dry_run": True}))

    assert job.state == "done", job.log
    assert job.lane == "read", "it writes nothing, so it may run beside other things"
    assert load_plan(app.album(source_id)[0]).to_dict() == before, "nothing was written"
    said = [line.strip() for line in job.log if "→" in line]
    assert said, job.log
    assert any(line.startswith("mbid: nothing → " + RELEASE) for line in said), said


def test_the_apply_is_a_write_and_does_it(collection):
    mb = SaysOneRelease()
    app = an_app(collection, mb)
    source_id = the_album(app)

    job = settled(app, app.submit("identify", {"id": source_id}))

    assert job.state == "done", job.log
    assert job.lane == "write"
    after = load_plan(app.album(source_id)[0])
    assert after.mbid == RELEASE, "the release it was identified as"
    assert all(t.mbid for t in after.tracks), "and a recording id per track"


def test_a_matched_release_outranks_the_files_own_tags(collection):
    """§9, slice 148 (the user: *"normalizing a library may become quite a workload otherwise"*).
    Until R-515 the files' own tags beat the archive for an adopted album, so a release cased
    differently brought its ids and left the name alone. Now a release that has been **matched** —
    by name, by artist and by four fifths of its titles — is the better information, and the album
    takes its spelling. Nothing is written without an apply."""
    mb = SaysOneRelease(title="CULT")
    app = an_app(collection, mb)
    source_id = the_album(app)

    settled(app, app.submit("identify", {"id": source_id}))

    after = load_plan(app.album(source_id)[0])
    assert after.album == "CULT", "the matched release's own title"
    assert after.auto.get("album") == "CULT"
    assert after.mbid == RELEASE


def test_the_album_is_looked_at_even_when_the_cheap_check_would_skip_it(collection):
    """The point of the button is to look, so an album `update` would call unchanged is still read."""
    mb = SaysOneRelease()
    app = an_app(collection, mb)
    source_id = the_album(app)

    settled(app, app.submit("identify", {"id": source_id, "dry_run": True}))

    assert mb.searched == 1, "it really asked"


def test_only_that_album_is_touched(collection, one_second_of_sound):
    an_album(collection / "Apocalyptica" / "Reflections", ["Somewhere Around Nothing"],
             one_second_of_sound, artist="Apocalyptica", album="Reflections")
    intake.take_in(a_library(collection), collection, QUIET, dry_run=False, log=lambda s: None)

    mb = SaysOneRelease()
    app = an_app(collection, mb)
    mine = next(a["id"] for a in app.state()["albums"] if a["album"] == "Cult")

    settled(app, app.submit("identify", {"id": mine}))

    other = load_plan(collection / "Apocalyptica" / "Reflections")
    assert other is not None and other.album == "Reflections"
    assert other.mbid is None, "it was never looked up"


def test_a_pinned_release_is_the_one_asked_about(collection):
    mb = SaysOneRelease()
    app = an_app(collection, mb)
    source_id = the_album(app)
    album_dir = app.album(source_id)[0]
    plan = load_plan(album_dir)
    plan.mbid, plan.provenance["mbid"] = RELEASE, "user"
    save_plan(plan, album_dir)
    app.library_changed()

    settled(app, app.submit("identify", {"id": source_id, "dry_run": True}))

    assert mb.searched == 0, "a pin is not searched for"
    assert mb.opened == [RELEASE], mb.opened


def test_a_field_the_user_typed_is_not_overwritten(collection):
    mb = SaysOneRelease(title="CULT")
    app = an_app(collection, mb)
    source_id = the_album(app)
    album_dir = app.album(source_id)[0]
    plan = load_plan(album_dir)
    plan.album, plan.provenance["album"] = "What I Call It", "user"
    save_plan(plan, album_dir)
    app.library_changed()

    settled(app, app.submit("identify", {"id": source_id}))

    assert load_plan(app.album(source_id)[0]).album == "What I Call It"


def test_an_unknown_album_is_refused(collection):
    app = an_app(collection, SaysOneRelease())
    with pytest.raises(ValueError, match="unknown album"):
        app.submit("identify", {"id": "no-such-album"})


# -- the endpoint --------------------------------------------------------------------------------


def test_the_endpoint_needs_the_write_header(collection):
    import threading

    import httpx

    app = an_app(collection, SaysOneRelease())
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10)
    source_id = c.get("/api/state").json()["albums"][0]["id"]

    assert c.post("/api/identify", json={"id": source_id}).status_code == 403, "no write header"
    r = c.post("/api/identify", json={"id": source_id, "dry_run": True}, headers=HDR)
    assert r.status_code == 202, r.text
    assert r.json()["job"]["kind"] == "identify"
    assert r.json()["job"]["lane"] == "read"
    bad = c.post("/api/identify", json={"id": "no-such-album", "dry_run": True}, headers=HDR)
    assert bad.status_code == 400 and "unknown album" in bad.text
    c.close()
    srv.shutdown()


# -- what counts as a change, and what the apply says it wrote (§9, slice 154; R-519 item 1) -----


def test_the_check_hands_over_its_list_of_changes(collection):
    """The user: *Identify said "matched" and wrote nothing visible.* The page was counting the
    pass's progress as changes, because it read them out of the log — which also carries `reading
    …`, `left alone: …`, `MusicBrainz: release matched`. The outcome carries the list itself."""
    app = an_app(collection, SaysOneRelease())
    source_id = the_album(app)

    job = settled(app, app.submit("identify", {"id": source_id, "dry_run": True}))

    outcome = job.result[0]
    assert outcome["status"] == "reported"
    assert outcome["changes"], "the list the panel offers to apply"
    assert outcome["message"] == f"{len(outcome["changes"])} change(s)"
    assert any(line.startswith("mbid: nothing → " + RELEASE) for line in outcome["changes"])
    for noise in ("reading", "left alone", "MusicBrainz:", "existing album", "only:"):
        assert not any(line.startswith(noise) for line in outcome["changes"]), noise


def test_an_album_already_identified_has_nothing_to_change(collection):
    """And then the count is nought, which is what makes the panel's "MusicBrainz agrees with what
    this album already says" reachable at all. Its log still has its six progress lines."""
    app = an_app(collection, SaysOneRelease())
    source_id = the_album(app)
    settled(app, app.submit("identify", {"id": source_id}))        # apply it first

    job = settled(app, app.submit("identify", {"id": source_id, "dry_run": True}))

    outcome = job.result[0]
    assert outcome["changes"] == [], job.log
    assert outcome["message"] == "0 change(s)"
    assert len(job.log) > 1, "the log still says what it did; only the change list is empty"


def test_the_apply_reports_what_it_wrote(collection):
    """Pressing Apply used to answer `downloading 0 of 4 tracks` and `4/4 tracks done` — nothing
    about the release it had written, the cover it had fetched or the titles it had rewritten."""
    app = an_app(collection, SaysOneRelease())
    source_id = the_album(app)
    check = settled(app, app.submit("identify", {"id": source_id, "dry_run": True}))

    job = settled(app, app.submit("identify", {"id": source_id}))

    assert job.state == "done", job.log
    wrote = job.result[0]["changes"]
    assert wrote, "the apply says what it wrote"
    assert wrote == check.result[0]["changes"], "the same list the check offered, so they can be read together"
    assert any(line.startswith("mbid: nothing → " + RELEASE) for line in wrote)
    assert any("mbid: nothing → " + RELEASE in line for line in job.log), \
        "and it is in the log the person watching sees"
