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
import time
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

    monkeypatch.setattr(service_mod, "timing_provider", lambda _cfg, _what="": fake)
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


# -- two slots, because the two jobs are bought in different places (§9.40) --------------------


def test_one_provider_in_the_old_key_still_means_both():
    from ytalbum.timing import TRANSCRIBE, kind_for

    cfg = Config(timing_provider="local")
    assert kind_for(cfg, ALIGN) == "local" and kind_for(cfg, TRANSCRIBE) == "local"
    assert kind_for(cfg) == "local"  # asked without a capability at all
    assert kind_for(Config()) == "none"


def test_each_slot_wins_for_its_own_capability():
    from ytalbum.timing import TRANSCRIBE, kind_for

    cfg = Config(timing_provider="none", timing_align_provider="local", timing_draft_provider="deepgram")
    assert kind_for(cfg, ALIGN) == "local"
    assert kind_for(cfg, TRANSCRIBE) == "deepgram"


def test_an_empty_slot_falls_back_and_a_filled_one_overrides():
    from ytalbum.timing import TRANSCRIBE, kind_for

    # the shape a config takes on the way from one setting to two: one slot written, one not
    cfg = Config(timing_provider="local", timing_draft_provider="deepgram")
    assert kind_for(cfg, ALIGN) == "local"          # untouched, still the old key
    assert kind_for(cfg, TRANSCRIBE) == "deepgram"  # the new slot


def test_what_the_page_may_offer_is_the_union_of_both_slots(tmp_path, monkeypatch):
    from ytalbum.timing import TRANSCRIBE, can, capabilities_of

    cfg = Config(timing_align_provider="local", timing_draft_provider="deepgram",
                 timing_deepgram_key="k")
    monkeypatch.setattr("ytalbum.timing_local.has_whisper", lambda: False)
    # local can align (the extra decides at runtime; here it is faked as installed but without
    # the second one), deepgram can only transcribe — and both are true at once, which no single
    # provider could have said
    monkeypatch.setattr(LocalTiming, "capabilities", lambda self: frozenset({ALIGN}))
    assert capabilities_of(cfg) == frozenset({ALIGN, TRANSCRIBE})
    assert can(cfg, ALIGN) and can(cfg, TRANSCRIBE)
    # and the drafting slot cannot align, whatever the aligning slot can do
    assert "align" not in provider(cfg, TRANSCRIBE).capabilities()


def test_the_config_reads_both_slots_from_a_table_too(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[timing]\nprovider = "none"\nalign_provider = "local"\ndraft_provider = "deepgram"\n')
    cfg = load(path)
    assert cfg.timing_align_provider == "local" and cfg.timing_draft_provider == "deepgram"
    assert cfg.timing_provider == "none"


def test_a_slot_offers_only_what_that_kind_could_ever_do():
    from ytalbum.timing import OFFERS, TRANSCRIBE

    assert ALIGN not in OFFERS["deepgram"]        # transcribes, and the panel must not offer it
    assert TRANSCRIBE in OFFERS["deepgram"]
    assert OFFERS["none"] == ()
    for kind in ("local", "http", "elevenlabs"):  # these depend on what is installed, not on kind
        assert ALIGN in OFFERS[kind] and TRANSCRIBE in OFFERS[kind]


# -- giving the graphics card back (§9.41, backlog 18) ----------------------------------------


class Loadable:
    """A provider that has models, for the timer to let go of."""

    def __init__(self) -> None:
        self.released = 0

    def release(self) -> bool:
        self.released += 1
        return True


def captured_watcher(monkeypatch) -> list:
    """The helper starts a daemon thread; a test wants to drive it by hand, on a fake clock."""
    watcher: list = []
    monkeypatch.setattr("ytalbum.timing_serve.threading.Thread",
                        lambda target, name=None, daemon=None:
                        types.SimpleNamespace(start=lambda: watcher.append(target)))
    return watcher


def test_the_idle_timer_lets_go_after_the_configured_quiet(monkeypatch):
    from ytalbum.timing_serve import idle_release

    engine, clock, sleeps = Loadable(), [0.0], []
    watcher = captured_watcher(monkeypatch)

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds
        if len(sleeps) > 20:
            raise SystemExit  # the watcher runs for ever; this is how a test gets off

    idle_release(engine, minutes=1, sleep=sleep, now=lambda: clock[0])
    with pytest.raises(SystemExit):
        watcher[0]()
    assert engine.released >= 1
    assert all(s == 15.0 for s in sleeps)  # it looks four times per idle period, not constantly


def test_a_request_postpones_the_letting_go(monkeypatch):
    from ytalbum.timing_serve import idle_release

    engine, clock, ticks = Loadable(), [0.0], []
    watcher = captured_watcher(monkeypatch)

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        ticks.append(clock[0])
        with idle:                    # something is aligned on every tick
            clock[0] += 1
        if len(ticks) > 30:
            raise SystemExit

    idle = idle_release(engine, minutes=1, sleep=sleep, now=lambda: clock[0])
    with pytest.raises(SystemExit):
        watcher[0]()
    assert engine.released == 0        # a machine in use never has its models taken away


def test_a_request_still_running_keeps_its_models(monkeypatch):
    """The first live try took the separator out of a running alignment (catalog AB): a six-second
    window and a ten-second job. Being *in* a request is not the same as having finished one."""
    from ytalbum.timing_serve import idle_release

    engine, clock, ticks = Loadable(), [0.0], []
    watcher = captured_watcher(monkeypatch)

    def sleep(seconds: float) -> None:
        clock[0] += seconds           # time passes, far beyond the idle window
        ticks.append(clock[0])
        if len(ticks) > 5:
            raise SystemExit

    idle = idle_release(engine, minutes=1, sleep=sleep, now=lambda: clock[0])
    with idle, pytest.raises(SystemExit):   # a long request, held open the whole time
        watcher[0]()
    assert engine.released == 0
    # and once it is over, the next quiet period does let go
    with pytest.raises(SystemExit):
        ticks.clear()
        watcher[0]()
    assert engine.released == 1


def test_zero_minutes_means_never(monkeypatch):
    from ytalbum.timing_serve import idle_release

    engine = Loadable()
    watcher = captured_watcher(monkeypatch)
    idle = idle_release(engine, minutes=0)
    with idle:                         # still usable, and does nothing at all
        pass
    assert watcher == [] and engine.released == 0   # no thread was even started


def test_releasing_without_torch_is_a_dictionary_lookup(monkeypatch):
    """An installation with no timing provider must not import 1.5 GB of torch to free nothing."""
    from ytalbum.timing import release_gpu_memory

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert release_gpu_memory() is False


def test_releasing_empties_the_pool_when_torch_is_here(monkeypatch):
    from ytalbum.timing import release_gpu_memory

    emptied: list[int] = []
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: True, empty_cache=lambda: emptied.append(1))))
    assert release_gpu_memory() is True and emptied == [1]
    # and on a machine with no card there is nothing to do
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: emptied.append(1))))
    assert release_gpu_memory() is False and emptied == [1]


def test_the_provider_lets_go_of_what_it_loaded(monkeypatch):
    engine, said = LocalTiming(), []
    engine.log = said.append
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)))
    assert engine.release() is False          # nothing was loaded; nothing is said
    assert said == []
    engine._models, engine._separator = {"en": object()}, object()
    assert engine.release() is True
    assert engine._models == {} and engine._separator is None
    assert any("next request loads them again" in line for line in said)


def test_the_idle_minutes_are_configurable(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("timing_idle_minutes = 0\n")
    assert load(path).timing_idle_minutes == 0.0          # 0 = keep them for ever
    assert load(tmp_path / "none.toml").timing_idle_minutes == 5.0


# -- telling which method lost the song (§9.44) -------------------------------------------------


def a_song(lines: int = 40, every: float = 5.0) -> list[float]:
    """Stamps of a method that followed the song: one line every few seconds, in order."""
    return [10.0 + i * every for i in range(lines)]


SUNG = [(9.0, 215.0)]          # one long sung stretch, as most songs are


def test_a_method_that_followed_the_song_looks_like_one():
    from ytalbum.timing import lost, signals_of

    got = signals_of(a_song(), length=220.0, sung=SUNG)
    assert got.placed == 40 and got.in_silence == 0 and got.backwards == 0 and got.past_end == 0
    assert lost(got) == ""


def test_stamps_that_do_not_cover_the_singing_are_what_lost_looks_like():
    """The signal the sixteen tracks chose (§9.44): a lyric spans the singing, or it is elsewhere."""
    from ytalbum.timing import lost, signals_of

    # the whole lyric squeezed into a minute of a three-and-a-half minute song
    got = signals_of([100.0 + i * 1.2 for i in range(40)], length=260.0, sung=SUNG)
    assert got.span is not None and got.span < 0.3
    assert "cover only 23% of the part of the track where somebody sings" in lost(got)


def test_stamps_piled_on_each_other_are_the_other_shape_of_lost():
    from ytalbum.timing import lost, signals_of

    piled = a_song(30) + [200.0 + i * 0.05 for i in range(10)]
    got = signals_of(piled, length=260.0, sung=SUNG)
    assert got.piled == 9
    assert "piled on top of each other" in lost(got)


def test_silence_is_recorded_and_never_judged():
    """Measured and dropped as a rule: these tracks are 55-86% singing, so a method that is
    somewhere else entirely still lands inside singing (catalog AE)."""
    from ytalbum.timing import lost, signals_of

    got = signals_of([216.0 + i * 0.4 for i in range(40)], length=260.0, sung=SUNG)
    assert got.in_silence == 40        # all of them, and it is still not what decides
    assert "nobody sings" not in lost(got)


def test_stamps_going_backwards_are_not_a_song():
    from ytalbum.timing import lost, signals_of

    # a song whose stamps do span the singing, so that only the ordering is odd
    order = a_song(40)
    shuffled = order[20:] + order[:20]          # the second half placed before the first
    got = signals_of(shuffled, length=220.0, sung=SUNG)
    assert got.backwards == 1  # one break, at the seam — and one is not a quarter of forty
    assert lost(got) == ""
    zigzag = [v for pair in zip(order[:20], reversed(order[20:]), strict=False) for v in pair]
    assert "stamps go backwards" in lost(signals_of(zigzag, length=220.0, sung=SUNG))


def test_stamps_past_the_end_of_the_track_are_impossible():
    from ytalbum.timing import lost, signals_of

    got = signals_of([100.0, 101.0, 300.0, 310.0, 320.0], length=220.0, sung=SUNG)
    assert got.past_end == 3
    assert "past the end of the track" in lost(got)


def test_a_single_stamp_is_not_evidence_of_anything():
    from ytalbum.timing import lost, signals_of

    got = signals_of([216.0], length=260.0, sung=SUNG)
    assert got.span is None and lost(got) == ""      # nothing spans anything on its own
    assert lost(signals_of([], length=260.0, sung=SUNG)) == ""


def test_which_lost_only_answers_when_exactly_one_of_them_did():
    from ytalbum.timing import signals_of, which_lost

    good = signals_of(a_song(), length=220.0, sung=SUNG)
    bad = signals_of([100.0 + i * 1.2 for i in range(40)], length=260.0, sung=SUNG)
    assert which_lost(good, bad)[0] == "second"
    assert "cover only" in which_lost(good, bad)[1]
    assert which_lost(bad, good)[0] == "first"
    assert which_lost(good, good) == ("", "")   # neither: no evidence to prefer one
    assert which_lost(bad, bad) == ("", "")     # both: no evidence to prefer one


def test_the_whole_track_case_keeps_the_method_the_evidence_favours():
    from ytalbum.timing import signals_of, verified

    ours = Timed(lines=[TimedLine(f"line {i}", 10.0 + i * 5) for i in range(40)],
                 provider="local", model="wav2vec2")
    theirs = Timed(lines=[TimedLine(f"line {i}", 100.0 + i * 1.2) for i in range(40)],
                   provider="local", model="large-v3")
    good = signals_of([line.start for line in ours.lines], length=220.0, sung=SUNG)
    bad = signals_of([line.start for line in theirs.lines], length=260.0, sung=SUNG)

    # the second method lost it: the first one's stamps are kept, and the notice can say which
    got = verified(ours, theirs, evidence=(good, bad))
    assert [line.start for line in got.lines] == [line.start for line in ours.lines]
    assert got.parameters["lost_method"] == "large-v3"
    assert "cover only" in got.parameters["lost_why"]
    assert got.parameters["kept_method"] == "wav2vec2"
    assert "one_method" not in got.parameters

    # and when the *primary* is the lost one, the second method's stamps are kept instead —
    # which is the whole point of P33: no more trusting the primary by policy
    other = verified(theirs, ours, evidence=(bad, good))
    assert [line.start for line in other.lines] == [line.start for line in ours.lines]
    assert other.parameters["lost_method"] == "large-v3"
    assert other.parameters["kept_method"] == "wav2vec2"


def test_without_evidence_the_old_policy_stands():
    from ytalbum.timing import verified

    ours = Timed(lines=[TimedLine(f"line {i}", 10.0 + i * 5) for i in range(6)], provider="local", model="wav2vec2")
    theirs = Timed(lines=[TimedLine(f"line {i}", 200.0 + i) for i in range(6)], provider="local", model="large-v3")
    got = verified(ours, theirs)
    assert [line.start for line in got.lines] == [line.start for line in ours.lines]
    assert got.parameters["one_method"] == "a second method disagreed about the whole track"
    assert "lost_method" not in got.parameters


def test_a_saved_alignment_keeps_both_methods_figures(album, tmp_path, monkeypatch):
    """The library accumulates the evidence sixteen tracks cannot give (§9.44)."""
    from ytalbum.web import App

    album_dir, plan, yt = album
    app = App(Config(), tmp_path, service_factory=lambda job: Service(Config(), tmp_path, yt=yt, log=job.log.append))
    track = plan.tracks[0]
    job = app.submit("save_lyrics", {"id": plan.source_id, "video_id": track.video_id,
                                     "text": "[00:01.0] a line", "timed_by": "local/wav2vec2",
                                     "checked": {"first_span": "0.98", "second_span": "0.41",
                                                 "second_piled": "12", "lost_method": "large-v3",
                                                 "kept_method": "wav2vec2",
                                                 "colour": "not one of ours"}})
    for _ in range(100):
        if job.state not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert job.state == "done", job.log
    saved = load_plan(album_dir).tracks[0]
    assert saved.lyrics_checked == {"first_span": "0.98", "second_span": "0.41", "second_piled": "12",
                                    "lost_method": "large-v3", "kept_method": "wav2vec2"}
    assert saved.lyrics_timed_by == "local/wav2vec2"
