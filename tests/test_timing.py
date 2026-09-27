"""The timing boundary, offline: a fake provider is enough to test everything but the model.

The real models are opt-in (`YTALBUM_TIMING_LIVE=1` and the `ytalbum[timing]` extra installed);
without them this file still covers the shape of the thing — what the page is told it may offer,
what the job returns, what reaches the disk, and what the HTTP provider does with an endpoint that
answers and one that does not.
"""

import json
import os
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config, load
from ytalbum.download import load_plan, run, save_plan
from ytalbum.plan import build_plan
from ytalbum.service import Service
from ytalbum.timing import (
    ALIGN,
    HttpTiming,
    NoTiming,
    Timed,
    TimedLine,
    TimingUnavailable,
    capabilities_of,
    language_of,
    plain_lines,
    provider,
)


class FakeTiming:
    """A provider that places every second line, so `unplaced` is exercised by default."""

    name = "fake"

    def __init__(self, gap: float = 3.0, skip_every: int = 2) -> None:
        self.gap, self.skip_every, self.calls = gap, skip_every, []
        self.log = lambda _: None

    def capabilities(self) -> frozenset[str]:
        return frozenset({ALIGN})

    def align(self, audio, lines, *, language=None, check=None):
        self.calls.append((Path(audio).name, tuple(lines), language))
        if check:
            check()
        placed = [TimedLine(text=line, start=None if (i + 1) % self.skip_every == 0 else round(i * self.gap, 2),
                            end=None) for i, line in enumerate(lines)]
        return Timed(lines=placed, provider=self.name, model="ruler", version="1",
                     parameters={"language": language or "?"})

    def transcribe(self, audio, *, language=None, check=None):
        raise TimingUnavailable("the fake only aligns")


# -- the default: nothing, and it says so usefully --------------------------------------------


def test_nothing_is_configured_and_nothing_is_offered():
    cfg = Config()
    assert cfg.timing_provider == "none"
    assert isinstance(provider(cfg), NoTiming)
    assert capabilities_of(cfg) == frozenset()
    with pytest.raises(TimingUnavailable, match="No timing provider is configured"):
        provider(cfg).align(Path("x.opus"), ["a"])


def test_an_http_provider_without_an_endpoint_is_refused():
    cfg = Config(timing_provider="http", timing_endpoint=None)
    assert capabilities_of(cfg) == frozenset()  # and the page therefore offers nothing
    with pytest.raises(TimingUnavailable, match="timing_endpoint is empty"):
        provider(cfg)


def test_the_config_reads_flat_keys_and_a_table(tmp_path):
    flat = tmp_path / "flat.toml"
    flat.write_text('timing_provider = "http"\ntiming_endpoint = "http://box:8770"\ntiming_device = "cpu"\n')
    cfg = load(flat)
    assert (cfg.timing_provider, cfg.timing_endpoint, cfg.timing_device) == ("http", "http://box:8770", "cpu")

    table = tmp_path / "table.toml"
    table.write_text('[timing]\nprovider = "local"\ndevice = "cuda"\n')
    cfg = load(table)
    assert (cfg.timing_provider, cfg.timing_device) == ("local", "cuda")
    assert load(tmp_path / "missing.toml").timing_provider == "none"


# -- the words that go to a provider -----------------------------------------------------------


def test_only_words_are_sent():
    text = "[00:12.30] one\n\n  \n[01:02.5]two\nthree   \n"
    assert plain_lines(text) == ["one", "two", "three"]
    assert plain_lines("") == []


def test_the_language_comes_from_the_words():
    assert language_of(["Ich bin ein Bösewicht", "und das ist nicht schön"]) == "de"
    assert language_of(["We are the ones who see no tomorrow"]) == "en"
    assert language_of(["Lalala"]) == "en"  # nothing to go on: the default, not a crash


# -- through the service -------------------------------------------------------------------


@pytest.fixture
def album(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    return album_dir, load_plan(album_dir), yt


def service_with(cfg, tmp_path, yt, fake, monkeypatch):
    import ytalbum.service as service_mod

    monkeypatch.setattr(service_mod, "timing_provider", lambda _cfg: fake)
    return Service(cfg, tmp_path, yt=yt, log=lambda s: None)


def test_align_returns_a_proposal_and_writes_nothing(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    track = plan.tracks[0]
    fake = FakeTiming()
    service = service_with(Config(timing_provider="local"), tmp_path, yt, fake, monkeypatch)
    before = sorted((p.name, p.stat().st_mtime_ns) for p in album_dir.iterdir())

    result = service.align_lyrics(plan.source_id, track.video_id, "[00:01.0] one\n\ntwo\nthree")

    assert fake.calls == [(track.filename, ("one", "two", "three"), None)]
    timed = Timed.from_dict(result["timed"])
    assert [line.start for line in timed.lines] == [0.0, None, 6.0]
    assert timed.unplaced == [1]
    assert result["by"] == "fake/ruler 1" and result["placed"] == 2 and result["lines"] == 3
    assert sorted((p.name, p.stat().st_mtime_ns) for p in album_dir.iterdir()) == before


def test_align_refuses_what_it_cannot_do(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = service_with(Config(), tmp_path, yt, NoTiming(), monkeypatch)
    with pytest.raises(TimingUnavailable, match="cannot align"):
        service.align_lyrics(plan.source_id, plan.tracks[0].video_id, "one")
    with pytest.raises(ValueError, match="no such track"):
        service.align_lyrics(plan.source_id, "nope", "one")
    with pytest.raises(ValueError, match="no words"):
        service_with(Config(), tmp_path, yt, FakeTiming(), monkeypatch).align_lyrics(
            plan.source_id, plan.tracks[0].video_id, "   \n\n")


def test_a_pending_track_has_nothing_to_align_against(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    plan.tracks[0].state = "pending"
    save_plan(plan, album_dir)
    service = service_with(Config(), tmp_path, yt, FakeTiming(), monkeypatch)
    with pytest.raises(ValueError, match="no file to align against"):
        service.align_lyrics(plan.source_id, plan.tracks[0].video_id, "one")


def test_saving_records_whose_clock_it_is(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    track = plan.tracks[0]
    service = service_with(Config(), tmp_path, yt, FakeTiming(), monkeypatch)

    service.save_lyrics(plan.source_id, track.video_id, "[00:12.3] one", timed_by="local/wav2vec2 2.11")
    saved = load_plan(album_dir).tracks[0]
    assert saved.lyrics_timed_by == "local/wav2vec2 2.11"
    assert saved.provenance["lyrics"] == "user"  # the words are still the user's

    service.save_lyrics(plan.source_id, track.video_id, "[00:12.3] one typed by hand")
    assert load_plan(album_dir).tracks[0].lyrics_timed_by is None  # a hand save takes the mark off

    service.save_lyrics(plan.source_id, track.video_id, "", timed_by="local/wav2vec2")
    assert load_plan(album_dir).tracks[0].lyrics_timed_by is None  # and so does clearing them


# -- over HTTP ---------------------------------------------------------------------------------


@pytest.fixture
def served():
    """`ytalbum timing-serve`'s handler, with the fake behind it instead of a model."""
    from ytalbum.timing_serve import handler_for

    fake = FakeTiming()
    fake.resolved_device = lambda: "cpu"  # type: ignore[attr-defined]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", fake
    server.shutdown()
    server.server_close()


def test_the_far_end_answers_over_http(served, tmp_path, opus_template):
    endpoint, fake = served
    audio = tmp_path / "t.opus"
    shutil.copy(opus_template, audio)
    client = HttpTiming(endpoint)

    assert client.capabilities() == frozenset({ALIGN})
    timed = client.align(audio, ["one", "two", "three"], language="de")
    assert [line.text for line in timed.lines] == ["one", "two", "three"]
    assert timed.unplaced == [1]
    assert timed.provider == "fake" and timed.by == "fake/ruler 1"
    assert fake.calls[0][1] == ("one", "two", "three") and fake.calls[0][2] == "de"


def test_the_far_end_is_asked_about_itself_once(served, tmp_path, opus_template):
    endpoint, _ = served
    client = HttpTiming(endpoint)
    assert client.capabilities() == client.capabilities() == frozenset({ALIGN})
    assert client._capabilities is not None  # the cache is the point of this test


def test_an_endpoint_that_is_not_there_offers_nothing():
    cfg = Config(timing_provider="http", timing_endpoint="http://127.0.0.1:1")
    assert capabilities_of(cfg) == frozenset()  # the page shows no button rather than a broken one
    with pytest.raises(TimingUnavailable, match=r"could not be reached|did not answer"):
        HttpTiming("http://127.0.0.1:1").align(Path("x.opus"), ["one"])


def test_the_far_end_refuses_nonsense(served, tmp_path, opus_template):
    import httpx

    endpoint, _ = served
    assert httpx.get(f"{endpoint}/nope").status_code == 404
    assert httpx.post(f"{endpoint}/align", data={"lines": ""}).status_code in (400, 404)


# -- the real thing, only when asked for --------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("YTALBUM_TIMING_LIVE"),
                    reason="set YTALBUM_TIMING_LIVE=1 (and install ytalbum[timing]) to run the models")
def test_the_local_provider_aligns_for_real(tmp_path, opus_template):
    from ytalbum.timing_local import LocalTiming

    engine = LocalTiming(device="cpu")
    assert ALIGN in engine.capabilities()
    audio = tmp_path / "t.opus"
    shutil.copy(opus_template, audio)
    timed = engine.align(audio, ["hello"], language="en")
    assert len(timed.lines) == 1 and timed.provider == "local"
