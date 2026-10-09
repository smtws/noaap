"""Completing an album from its pinned release (DESIGN §9, slice 162; P118). A fake provider, no
live YouTube: the one live proof is a disposable copy of `Letzte Instanz/Heilig` (R-563)."""


import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap.complete import Slot, gaps, parse_only, rank, same_song, score
from noaap.config import Config
from noaap.download import load_plan, run, save_plan
from noaap.models import Provenance
from noaap.plan import build_plan
from noaap.service import Service


class Finder(FakeYouTube):
    """The fake provider, able to look for one recording: each query answers with a hit whose title
    names the song the query asked for, as an upload of it would."""

    def __init__(self, template, answers):
        super().__init__(template)
        self.answers, self.asked = answers, []

    delivers = "opus"

    def capabilities(self):
        return super().capabilities() | {"tracks"}

    def find_tracks(self, query, limit=8):
        self.asked.append(query)
        return [hit for words, hits in self.answers.items() if words in query for hit in hits]


class FakeMB:
    def __init__(self, release):
        self.given = release

    def release(self, mbid):
        return self.given if mbid == self.given["id"] else None


def release_of(plan, more=(), video=()):
    """The album's own tracks as one medium, plus `more` (title, seconds) after them; `video` as a
    DVD medium of its own."""
    tracks = [{"position": t.number, "title": t.title, "length": 200_000,
               "recording": {"id": f"rec-{t.number}", "title": t.title}} for t in plan.tracks]
    n = len(tracks)
    tracks += [{"position": n + i, "title": title, "length": int(sec * 1000),
                "recording": {"id": f"rec-new-{i}", "title": title}} for i, (title, sec) in enumerate(more, 1)]
    media = [{"position": 1, "format": "CD", "tracks": tracks}]
    if video:
        media.append({"position": 2, "format": "DVD-Video",
                      "tracks": [{"position": 1, "title": v, "recording": {"id": "rec-dvd", "title": v}} for v in video]})
    return {"id": "rel-1", "title": plan.album, "media": media}


@pytest.fixture
def album(tmp_path, opus_template):
    plan = build_plan(vol1())
    run(plan, tmp_path / plan.folder, FakeYouTube(opus_template))
    album_dir = tmp_path / plan.folder
    plan = load_plan(album_dir)
    plan.mbid, plan.provenance["mbid"] = "rel-1", Provenance.USER
    save_plan(plan, album_dir)
    return tmp_path, album_dir, plan


def service_for(lib, provider, release):
    return Service(Config(musicbrainz=True, lyrics=False), lib, yt=provider, mb=FakeMB(release))


def tree(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*") if p.is_file())


def test_no_pin_no_completion(album, opus_template):
    lib, album_dir, plan = album
    plan.provenance["mbid"] = Provenance.MB         # set by a pass, not by the user
    save_plan(plan, album_dir)
    finder = Finder(opus_template, {})
    out = service_for(lib, finder, release_of(plan, more=[("Gone Song", 200)])).complete(plan.source_id)
    assert out.status == "failed" and "no release pinned" in out.message
    assert finder.asked == []


def test_the_dry_run_names_the_hit_and_writes_nothing(album, opus_template):
    lib, album_dir, plan = album
    finder = Finder(opus_template, {"Gone Song": [
        {"ref": "good", "title": f"{plan.albumartist} - Gone Song", "channel": plan.albumartist, "length": 201},
        {"ref": "live", "title": f"{plan.albumartist} - Gone Song (Live)", "channel": "x", "length": 200}]})
    release = release_of(plan, more=[("Gone Song", 200)], video=["The Making Of"])
    before = tree(album_dir)
    out = service_for(lib, finder, release).complete(plan.source_id, dry_run=True)
    assert out.status == "dry"
    said = "\n".join(out.changes)
    assert f"1-{len(plan.tracks) + 1:02d} Gone Song (3:20): “{plan.albumartist} - Gone Song”" in said
    assert "2-01 The Making Of: a video on the release — not completed" in said
    assert "fetched as opus, beside the album's opus" in said
    assert tree(album_dir) == before and finder.downloads == []
    slot = next(s for s in out.offered if s["title"] == "Gone Song")
    assert [h["ref"] for h in slot["hits"]] == ["good"]          # the live version is not this recording


def test_the_apply_fetches_into_the_slot_and_leaves_the_rest_alone(album, opus_template):
    lib, album_dir, plan = album
    names = {t.video_id: t.filename for t in plan.tracks}
    finder = Finder(opus_template, {"Gone Song": [
        {"ref": "good", "title": f"{plan.albumartist} - Gone Song", "channel": plan.albumartist, "length": 201},
        {"ref": "also", "title": "Gone Song", "channel": "someone", "length": 205}]})
    out = service_for(lib, finder, release_of(plan, more=[("Gone Song", 200)])).complete(plan.source_id, dry_run=False)
    assert out.status == "ok", out.message
    assert finder.downloads == ["good"]
    after = load_plan(album_dir)
    new = next(t for t in after.tracks if t.video_id == "good")
    assert (new.disc, new.number, new.title, new.mbid, new.state) == (1, len(plan.tracks) + 1, "Gone Song", "rec-new-1", "done")
    assert new.provenance["title"] == Provenance.MB and Provenance.USER not in new.provenance.values()
    assert [c.ref for c in new.candidates] == ["good", "also"]       # the other hit waits beside it
    assert (album_dir / new.filename).exists()
    assert {t.video_id: t.filename for t in after.tracks if t.video_id in names} == names
    assert after.mbid == "rel-1" and after.provenance["mbid"] == Provenance.USER


def test_only_fetches_the_chosen_slots(album, opus_template):
    lib, album_dir, plan = album
    n = len(plan.tracks)
    finder = Finder(opus_template, {
        "First Gone": [{"ref": "one", "title": "First Gone", "channel": "c", "length": 200}],
        "Second Gone": [{"ref": "two", "title": "Second Gone", "channel": "c", "length": 200}]})
    release = release_of(plan, more=[("First Gone", 200), ("Second Gone", 200)])
    out = service_for(lib, finder, release).complete(plan.source_id, only={f"1-{n + 2:02d}"}, dry_run=False)
    assert out.status == "ok" and finder.downloads == ["two"]


def test_a_title_spelled_otherwise_is_not_missing(album, opus_template):
    lib, album_dir, plan = album
    release = release_of(plan)
    first = release["media"][0]["tracks"][0]
    first["title"] = f"A Suite – {plan.tracks[0].title}"          # the release's prefix (Nightwish)
    first["recording"]["id"] = "other"
    missing, extras = gaps(plan, release)
    assert missing == [] and extras == []


def test_an_album_with_renames_pending_is_held(album, opus_template):
    lib, album_dir, plan = album
    plan.tracks[0].filename, old = "renamed by hand.opus", album_dir / plan.tracks[0].filename
    old.rename(album_dir / "renamed by hand.opus")
    save_plan(plan, album_dir)
    finder = Finder(opus_template, {"Gone Song": [{"ref": "good", "title": "Gone Song", "channel": "c", "length": 200}]})
    out = service_for(lib, finder, release_of(plan, more=[("Gone Song", 200)])).complete(plan.source_id, dry_run=False)
    assert out.status == "held" and finder.downloads == []
    assert any("run `noaap repair` first" in line for line in out.changes)


def test_scoring_a_real_world_title():
    """The hit the live search gave for Heilig's missing track (R-563's proof)."""
    slot = Slot(disc=1, number=6, title="Für Dich", artist="Letzte Instanz", length=256.0)
    real = {"ref": "sA5e3HLPyWI", "title": "Letzte Instanz   Heilig   06   Fur Dich",
            "channel": "IronClad Metal & Rock Music", "length": 256}
    assert score(slot, real, "Heilig") == 2.0 + 2.0             # a typo apart (ü/u), length within 10 s
    other = {"ref": "x", "title": "Letzte Instanz \"Ohne Dich\" - unplugged", "channel": "c", "length": 267}
    assert score(slot, other, "Heilig") is None
    far = {**real, "ref": "far", "length": 400}
    assert [h["ref"] for h in rank(slot, [far, real], "Heilig")] == ["sA5e3HLPyWI"]


def test_same_song_and_parse_only():
    assert same_song("Vista", "All the Works of Nature Which Adorn the World – Vista")
    assert not same_song("Milk", "Milk - Exclusive Track")   # containment is not a rule (§9, slice 149)
    assert parse_only("1-06, 2-3 7") == {"1-06", "2-03", "1-07"}
    assert parse_only("") is None


def test_the_page_looks_first_and_fetches_only_what_was_ticked(album, opus_template):
    import threading
    import time

    import httpx

    from noaap.web import App

    lib, album_dir, plan = album
    n = len(plan.tracks)
    finder = Finder(opus_template, {
        "First Gone": [{"ref": "one", "title": "First Gone", "channel": "c", "length": 200}],
        "Second Gone": [{"ref": "two", "title": "Second Gone", "channel": "c", "length": 200}]})
    release = release_of(plan, more=[("First Gone", 200), ("Second Gone", 200)])
    cfg = Config(musicbrainz=True, lyrics=False)
    app = App(cfg, lib, port=0, service_factory=lambda job: Service(cfg, lib, log=job.log.append,
                                                                       yt=finder, mb=FakeMB(release)))
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    hdr = {"X-Noaap": "1"}

    def settled(c, job):
        for _ in range(200):
            j = c.get("/api/job", params={"id": job["id"]}).json()
            if j["state"] not in ("queued", "running"):
                return j
            time.sleep(0.05)

    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as c:
            check = c.post("/api/complete", headers=hdr, json={"id": plan.source_id, "dry_run": True}).json()["job"]
            assert check["lane"] == "read"
            got = settled(c, check)["result"]
            assert got["status"] == "dry" and [s["name"] for s in got["offered"]] == [f"1-{n + 1:02d}", f"1-{n + 2:02d}"]
            assert finder.downloads == []
            refused = c.post("/api/complete", headers=hdr, json={"id": plan.source_id})
            assert refused.status_code == 400                      # an apply names its tracks
            apply = c.post("/api/complete", headers=hdr, json={"id": plan.source_id, "only": [f"1-{n + 2:02d}"]}).json()["job"]
            assert apply["lane"] == "write" and settled(c, apply)["state"] == "done"
            assert finder.downloads == ["two"]
    finally:
        srv.shutdown()


def test_another_take_of_a_song_is_a_track_of_its_own():
    """`Heilig` lists `Schau in mein Gesicht` and `Schau in Mein Gesicht (Akkustik Version)`: holding
    the first does not hold the second, and neither answers for the other."""
    assert same_song("Schau in mein Gesicht", "Schau in Mein Gesicht")
    assert not same_song("Schau in mein Gesicht", "Schau in Mein Gesicht (Akkustik Version)")
    assert not same_song("Dreams (Deep Growl Mix)", "Dreams")
    assert same_song("Sanctus (Remastered)", "Sanctus")
