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
import sys
import threading
import types
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
from ytalbum.timing_local import LocalTiming


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


# -- a second opinion on an alignment (§9.38) ------------------------------------------------


def timed_at(*starts: float | None) -> Timed:
    return Timed(lines=[TimedLine(text=f"line {i}", start=s) for i, s in enumerate(starts)],
                 provider="local", model="wav2vec2")


def test_where_two_methods_agree_the_first_ones_number_is_kept():
    from ytalbum.timing import verified

    primary = timed_at(10.0, 20.0, 30.0)
    second = Timed(lines=[TimedLine("a", 10.4), TimedLine("b", 20.9), TimedLine("c", 31.5)],
                   provider="local", model="large-v3")
    got = verified(primary, second, threshold=1.0)
    # 0.4 s and 0.9 s apart is agreement and keeps the first method's number; 1.5 s is not
    assert [line.start for line in got.lines] == [10.0, 20.0, None]
    assert got.parameters["disagreed"] == "1" and got.parameters["compared"] == "3"
    assert got.parameters["verified_against"] == "large-v3"
    assert got.model == "wav2vec2 + large-v3"


def test_a_line_only_one_method_placed_is_not_a_disagreement():
    from ytalbum.timing import verified

    got = verified(timed_at(10.0, None), Timed(lines=[TimedLine("a", None), TimedLine("b", 50.0)],
                                               provider="local", model="large-v3"))
    assert [line.start for line in got.lines] == [10.0, None]
    assert got.parameters["compared"] == "0" and got.parameters["disagreed"] == "0"


def test_when_the_second_method_loses_the_song_the_primary_is_kept_whole():
    """The rule this shipped with placed nothing here. Catalog Y measured what that cost: on five of
    sixteen real tracks it fired, and on all five the primary was the accurate method."""
    from ytalbum.timing import verified

    primary = timed_at(10.0, 20.0, 30.0, 40.0)
    second = Timed(lines=[TimedLine("a", 99.0), TimedLine("b", 98.0), TimedLine("c", 97.0), TimedLine("d", 40.2)],
                   provider="local", model="large-v3")
    got = verified(primary, second)
    # every stamp kept, including the three the second method disagrees with: the per-line rule is
    # switched off in this regime, or the inversion would be hollow
    assert [line.start for line in got.lines] == [10.0, 20.0, 30.0, 40.0]
    assert got.parameters["one_method"] == "a second method disagreed about the whole track"
    assert got.parameters["lost"] == "3" and got.parameters["compared"] == "4"


def test_jitter_on_most_lines_is_not_a_lost_track(monkeypatch):
    """The two rules are separate constants on purpose (§9.38): being generous about jitter must not
    make a broken track look salvageable, and being strict about it must not condemn a good one."""
    from ytalbum.timing import verified

    primary = timed_at(10.0, 20.0, 30.0, 40.0)
    # three of four lines 3 s apart: past the 2 s agreement width, nowhere near losing the song
    second = Timed(lines=[TimedLine("a", 13.0), TimedLine("b", 23.0), TimedLine("c", 33.0), TimedLine("d", 40.2)],
                   provider="local", model="large-v3")
    got = verified(primary, second)
    assert "nothing_placed" not in got.parameters
    assert [line.start for line in got.lines] == [None, None, None, 40.0]  # the one they agree on
    assert got.parameters["disagreed"] == "3" and got.parameters["lost"] == "0"


def test_the_lost_width_is_configurable(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("timing_verify_lost = 8.0\n")
    assert load(path).timing_verify_lost == 8.0
    assert load(tmp_path / "none.toml").timing_verify_lost == 5.0


def test_the_threshold_is_configurable():
    from ytalbum.timing import verified

    primary, second = timed_at(10.0), Timed(lines=[TimedLine("a", 12.0)], provider="local", model="w")
    assert verified(primary, second, threshold=1.0).lines[0].start is None
    assert verified(primary, second, threshold=3.0).lines[0].start == 10.0


def test_verification_is_off_when_the_second_extra_is_not_installed(monkeypatch):
    from ytalbum.timing_local import LocalTiming

    monkeypatch.setattr("ytalbum.timing_local.has_whisper", lambda: False)
    assert LocalTiming().verifying() is False
    assert LocalTiming(verify=True).verifying() is False  # asked for, but there is nothing to ask
    monkeypatch.setattr("ytalbum.timing_local.has_whisper", lambda: True)
    assert LocalTiming().verifying() is True              # the default follows the install
    assert LocalTiming(verify=False).verifying() is False  # and can be turned off


def test_the_config_carries_the_verification_settings(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("timing_verify = false\ntiming_verify_threshold = 2.5\n")
    cfg = load(path)
    assert cfg.timing_verify is False and cfg.timing_verify_threshold == 2.5
    assert load(tmp_path / "none.toml").timing_verify is None  # unset means "whenever it is there"


def whisper_without_torch(monkeypatch, loaded: list[str], device: str = "cuda"):
    """Everything the second model needs, faked — and **nothing imported**.

    This file must pass in a venv with neither optional extra (the R-067 rule), so a test of the
    retry logic may not reach `torch`: `resolved_device` imports it to ask about the card, and
    `_free_vram` imports it to empty the cache. Patching both is what keeps these tests about the
    logic they are testing. CI caught the version that did not.
    """
    monkeypatch.setattr("ytalbum.timing_local.has_whisper", lambda: True)
    monkeypatch.setattr(LocalTiming, "resolved_device", lambda self: device)
    monkeypatch.setattr(LocalTiming, "_free_vram", lambda self: None)
    monkeypatch.setitem(sys.modules, "stable_whisper", types.SimpleNamespace(
        load_faster_whisper=lambda name, device, compute_type: loaded.append(device) or Loaded(device)))


class Loaded:
    """Stands in for a loaded Whisper model: it knows only which device it was put on."""

    def __init__(self, device: str) -> None:
        self.device = device


def test_the_cuda_trap_is_survived_where_it_actually_fires(monkeypatch):
    """ctranslate2 loads the model happily and only then finds no libcublas (§9.38).

    Found by running the cross-check for real on this laptop: torch brought CUDA 13, ctranslate2
    wanted 12, and the guard that sat around the *load* never saw it.
    """
    from ytalbum.timing_local import LocalTiming

    engine, said, loaded = LocalTiming(device="cuda"), [], []
    engine.log = said.append
    whisper_without_torch(monkeypatch, loaded)

    def run(model):
        if model.device == "cuda":
            raise RuntimeError("Library libcublas.so.12 is not found or cannot be loaded")
        return "placed"

    assert engine._whisper_run(run) == "placed"
    assert loaded == ["cuda", "cpu"]                     # one retry, on the processor
    assert engine._whisper_device == "cpu"
    assert any("libcublas" in line for line in said)     # and it says why it got slower


def test_a_failure_that_is_not_the_trap_is_not_retried(monkeypatch):
    from ytalbum.timing_local import LocalTiming

    engine, tries = LocalTiming(device="cuda"), []
    whisper_without_torch(monkeypatch, tries)
    with pytest.raises(RuntimeError, match="no kernel image"):
        engine._whisper_run(lambda model: (_ for _ in ()).throw(
            RuntimeError("no kernel image is available for execution on the device")))
    assert len(tries) == 1


def test_a_full_graphics_card_moves_the_check_and_keeps_going(monkeypatch):
    """8 GB does not hold the aligner, the separator and 3 GB of Whisper at once (section Y)."""
    from ytalbum.timing_local import LocalTiming

    engine, said, loaded = LocalTiming(device="cuda"), [], []
    engine.log = said.append
    whisper_without_torch(monkeypatch, loaded)

    def run(model):
        if model.device == "cuda":
            raise RuntimeError("CUDA failed with error out of memory")
        return "placed"

    assert engine._whisper_run(run) == "placed"
    assert loaded == ["cuda", "cpu"]
    assert any("no room left" in line for line in said)


def test_the_default_width_of_agreement_is_two_seconds():
    """Measured, not chosen: catalog Y's distances are jitter under ~1.5 s or 5 to 18 s apart."""
    from ytalbum.timing import VERIFY_THRESHOLD, verified

    assert VERIFY_THRESHOLD == 2.0
    primary = timed_at(10.0, 20.0)
    second = Timed(lines=[TimedLine("a", 11.4), TimedLine("b", 27.0)], provider="local", model="large-v3")
    # 1.4 s is Whisper being sloppy about a line the CTC pass had right; 7 s is one of them lost
    assert [line.start for line in verified(primary, second).lines] == [10.0, None]
