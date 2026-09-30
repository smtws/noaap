"""The app gives the graphics card back once it is idle (DESIGN §9, slice 82).

The numbers this is shaped around were measured on the laptop's card (RTX 4060, 8188 MiB total)
before any of it was written, and they are in `timing.py` beside the code:

    one alignment of a 4-minute track      3608 MiB held while the provider lives
    the aligner alone                       494 MiB
    the separator, having separated         854 MiB
    the second opinion alone               3776 MiB, none of it in torch's pool
    loading every model again, warm disk      ~2 s
    peak for one alignment                 3460 MiB reserved
    the same alignment on the processor      11.4 times the time

A stand-in card here: no model is loaded and no driver is asked. `test_the_real_card` does that, and
only when asked for.
"""
from __future__ import annotations

import os
import threading
import types

import pytest

from noaap import config
from noaap.timing import ALIGN, TRANSCRIBE, Engines, Idle, TimingUnavailable, release_when_idle


class Fake:
    """A provider that counts what it was asked to let go of, and never loads anything."""

    name = "fake"

    def __init__(self, what: str = "") -> None:
        self.what, self.released, self.loaded = what, 0, 1

    def capabilities(self) -> frozenset[str]:
        return frozenset({ALIGN, TRANSCRIBE})

    def release(self) -> bool:
        self.released += 1
        return True


def settings(**over) -> types.SimpleNamespace:
    base = {"timing_provider": "local", "timing_align_provider": "", "timing_draft_provider": "",
            "timing_device": "auto", "timing_verify": None, "timing_verify_threshold": 2.0,
            "timing_verify_lost": 5.0, "timing_endpoint": ""}
    return types.SimpleNamespace(**{**base, **over})


@pytest.fixture
def built(monkeypatch):
    """Every provider the holder builds, in order."""
    made: list[Fake] = []

    def build(cfg, what=""):
        made.append(Fake(what))
        return made[-1]

    monkeypatch.setattr("noaap.timing.provider", build)
    return made


# -- holding one provider between jobs (item 1) ----------------------------------------------------


def test_the_same_provider_comes_back_for_the_next_job(built):
    """This is the whole point: the models stay loaded, so track two does not pay two seconds again."""
    engines, cfg = Engines(), settings()

    first = engines.provider(cfg, ALIGN)
    again = engines.provider(cfg, ALIGN)

    assert first is again
    assert len(built) == 1, "one provider was built, not one per job"


def test_both_capabilities_share_one_provider_where_the_settings_agree(built):
    engines, cfg = Engines(), settings()

    aligner, drafter = engines.provider(cfg, ALIGN), engines.provider(cfg, TRANSCRIBE)

    assert aligner is drafter, "one `local` on the card, not two of it"
    assert engines.holding() == 1


def test_two_different_providers_are_both_held(built):
    engines = Engines()
    cfg = settings(timing_align_provider="local", timing_draft_provider="deepgram")

    aligner, drafter = engines.provider(cfg, ALIGN), engines.provider(cfg, TRANSCRIBE)

    assert aligner is not drafter and engines.holding() == 2


def test_changed_settings_let_the_old_provider_go_at_once(built):
    """Not at the next idle window: a provider nobody can ask for any more is holding the card."""
    engines, cfg = Engines(), settings()
    old = engines.provider(cfg, ALIGN)

    cfg.timing_device = "cpu"
    new = engines.provider(cfg, ALIGN)

    assert new is not old
    assert old.released == 1, "the models of the provider that was replaced went with it"
    assert engines.holding() == 1


def test_letting_go_releases_everything_it_holds(monkeypatch, built):
    freed = []
    monkeypatch.setattr("noaap.timing.release_gpu_memory", lambda: freed.append(True) or True)
    engines = Engines()
    cfg = settings(timing_align_provider="local", timing_draft_provider="deepgram")
    engines.provider(cfg, ALIGN)
    engines.provider(cfg, TRANSCRIBE)

    assert engines.let_go() is True
    assert [p.released for p in built] == [1, 1]
    assert freed == [True] and engines.holding() == 0


def test_a_provider_with_no_models_is_simply_dropped(monkeypatch):
    """`none`, `http` and the vendors hold nothing: letting go of them is not an error."""
    monkeypatch.setattr("noaap.timing.provider", lambda cfg, what="": object())
    monkeypatch.setattr("noaap.timing.release_gpu_memory", lambda: False)
    engines = Engines()
    engines.provider(settings(timing_provider="none"), ALIGN)

    assert engines.let_go() is False and engines.holding() == 0


def test_a_provider_that_fails_to_let_go_does_not_take_the_others_with_it(monkeypatch, built):
    monkeypatch.setattr("noaap.timing.release_gpu_memory", lambda: False)

    class Stubborn(Fake):
        def release(self):
            raise RuntimeError("the driver is in a mood")

    engines = Engines()
    cfg = settings(timing_align_provider="local", timing_draft_provider="deepgram")
    engines.provider(cfg, ALIGN)
    monkeypatch.setattr("noaap.timing.provider", lambda cfg, what="": Stubborn(what))
    engines.provider(cfg, TRANSCRIBE)

    assert engines.let_go() is True   # the first one was released
    assert engines.holding() == 0


# -- never while something is running or queued (item 3) -------------------------------------------


def test_nothing_is_released_while_a_job_holds_the_idle(monkeypatch):
    released, clock = [], [0.0]
    idle = Idle(now=lambda: clock[0])
    with idle:            # an earlier job, so something is loaded
        pass

    with idle:                     # a job is running
        clock[0] += 600            # and has been for ten minutes
        assert idle.release_if_quiet(60, lambda: released.append(True)) is False

    assert released == []


def test_nothing_is_released_before_the_window_has_passed():
    released, clock = [], [0.0]
    idle = Idle(now=lambda: clock[0])
    with idle:
        pass
    clock[0] += 59


    assert idle.release_if_quiet(60, lambda: released.append(True)) is False
    clock[0] += 1
    assert idle.release_if_quiet(60, lambda: released.append(True)) is True
    assert released == [True]


def test_a_queued_job_is_work_that_is_coming():
    """Nothing is running, the window has passed — and the next job is already in the queue."""
    released, clock = [], [0.0]
    idle = Idle(now=lambda: clock[0])
    with idle:            # a job has run, so the models are loaded
        pass
    clock[0] += 600

    assert idle.release_if_quiet(60, lambda: released.append(True), busy=lambda: True) is False
    assert idle.release_if_quiet(60, lambda: released.append(True), busy=lambda: False) is True


def test_a_job_cannot_start_in_the_middle_of_a_release():
    """The check and the release are one step, under the lock a job has to pass to start.

    Two steps is the bug this class was written for (`docs/qa-catalog.md`, section AB): a job that
    starts between them has its models taken away while it runs.
    """
    idle, order, inside, jobs = Idle(), [], threading.Event(), []
    with idle:                     # one job has been and gone, so there is something to release
        pass

    def release_slowly() -> None:
        order.append("release started")
        job = threading.Thread(target=enter)
        job.start()
        jobs.append(job)
        inside.wait(0.3)           # it must NOT get in while this is running
        order.append("release done")

    def enter() -> None:
        with idle:
            inside.set()
            order.append("job started")

    assert idle.release_if_quiet(0, release_slowly) is True
    jobs[0].join(2)

    assert order == ["release started", "release done", "job started"]


def test_an_idle_program_does_not_collect_its_garbage_once_a_minute_for_ever():
    """Nothing has been asked of it since the last release: there is nothing to give back again."""
    released, clock = [], [0.0]
    idle = Idle(now=lambda: clock[0])
    with idle:
        pass
    clock[0] += 600

    assert idle.release_if_quiet(60, lambda: released.append(True)) is True
    clock[0] += 600
    assert idle.release_if_quiet(60, lambda: released.append(True)) is False
    with idle:                         # one more job, and the next quiet period counts again
        pass
    clock[0] += 600
    assert idle.release_if_quiet(60, lambda: released.append(True)) is True
    assert len(released) == 2


def test_zero_seconds_starts_no_watcher(monkeypatch):
    threads = []
    monkeypatch.setattr("noaap.timing._in_the_background", threads.append)
    idle = release_when_idle(0, lambda: None)

    with idle:
        pass
    assert threads == [], "0 = hold the card for ever, and no thread to do nothing with"


# -- the app's own server (items 1 and 3) ----------------------------------------------------------


@pytest.fixture
def watcher(monkeypatch):
    """The idle watcher's loop body, captured instead of run in a thread."""
    caught: list = []
    monkeypatch.setattr("noaap.timing._in_the_background", caught.append)
    return caught


def jobs_with_a_clock(watcher, release, clock, idle_seconds=60.0):
    from noaap.web import Jobs

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        if clock[0] > 10_000:
            raise SystemExit   # the watcher runs for ever; this is how a test gets off

    return Jobs(lambda job: None, release=release, idle_seconds=idle_seconds,
                sleep=sleep, now=lambda: clock[0]), sleep


def test_the_card_goes_back_a_minute_after_the_last_job(watcher, monkeypatch):
    released, clock = [], [0.0]
    jobs, _ = jobs_with_a_clock(watcher, lambda: released.append(clock[0]), clock)
    with jobs.idle:                       # one job, taking ten seconds
        clock[0] += 10

    with pytest.raises(SystemExit):
        watcher[0]()

    assert released, "the card was never given back"
    assert released[0] - 10 >= 60, "it waited the full window first"
    assert len(released) == 1, "and only once: an idle program has nothing to give back twice"


def test_the_card_is_not_taken_from_a_running_job(watcher):
    released, clock = [], [0.0]
    jobs, _ = jobs_with_a_clock(watcher, lambda: released.append(clock[0]), clock)

    with jobs.idle, pytest.raises(SystemExit):   # a long job, held open the whole time
        watcher[0]()

    assert released == []


def test_a_queued_job_holds_the_card(watcher):
    released, clock = [], [0.0]
    jobs, _ = jobs_with_a_clock(watcher, lambda: released.append(clock[0]), clock)
    jobs.submit("align", "Align something", lambda s: None)   # queued: no worker runs it here
    with pytest.raises(SystemExit):
        watcher[0]()

    assert released == [], "a job waiting its turn will want the models that are loaded"


def test_no_release_at_all_where_nobody_asked_for_one(watcher):
    from noaap.web import Jobs

    Jobs(lambda job: None)   # no `release`: the CLI's Jobs, and the tests' — no watcher, no thread
    assert watcher == []


def test_the_app_starts_one_watcher_for_the_card(watcher, tmp_path):
    from noaap.config import Config
    from noaap.web import App

    App(Config(), tmp_path)

    assert len(watcher) == 1, "one server, one timer"


def test_and_none_at_all_when_the_window_is_zero(watcher, tmp_path):
    from noaap.config import Config
    from noaap.web import App

    cfg = Config()
    cfg.timing_card_idle_seconds = 0
    App(cfg, tmp_path)

    assert watcher == [], "0 = hold the models for ever, and no thread to do nothing with"


def test_no_test_leaves_a_watcher_running(tmp_path):
    """The canary for a real defect (§9, slice 84, catalog BX).

    An `App`'s watcher outlives the test that made it, and when its window passes it calls
    `release_gpu_memory()` — inside whichever *later* test has stubbed `sys.modules` with a fake
    torch. That is how `test_releasing_empties_the_pool_when_torch_is_here` came to count three calls
    instead of one on CI, with 59 of these threads alive after three test files. `conftest.isolated`
    makes the line that starts them do nothing; this is what says so out loud.
    """
    import threading

    from noaap.config import Config
    from noaap.web import App

    App(Config(), tmp_path)

    assert [t.name for t in threading.enumerate() if t.name == "noaap-card-idle"] == []


# -- a full card, and a card that fills up half way through (item 2) -------------------------------


def on_a_card(free: int, total: int = 8188, held: int = 0, **kw):
    """A `LocalTiming` that thinks it is on a card with this much room, loading nothing."""
    from noaap.timing_local import LocalTiming

    engine = LocalTiming(device="cuda", **kw)
    engine.resolved_device = lambda: "cuda"          # type: ignore[method-assign]
    said: list[str] = []
    engine.log = said.append
    engine.said = said                               # type: ignore[attr-defined]
    return engine, free, total, held


@pytest.fixture
def card(monkeypatch):
    def make(free: int, total: int = 8188, held: int = 0, **kw):
        engine, free, total, held = on_a_card(free, total, held, **kw)
        monkeypatch.setattr("noaap.timing_local.card_room", lambda: (free, total))
        monkeypatch.setattr("noaap.timing_local.pool_held", lambda: held)
        return engine
    return make


def test_room_on_the_card_means_the_card(card):
    engine = card(free=4000)
    assert engine.device_now(ALIGN) == "cuda"
    assert engine.said == [], "nothing to say when the answer is the usual one"


def test_a_full_card_sends_the_job_to_the_processor_and_says_so(card):
    engine = card(free=900)

    assert engine.device_now(ALIGN) == "cpu"
    assert len(engine.said) == 1
    said = engine.said[0]
    assert "900 MiB free of 8188" in said and "3500" in said
    assert "processor" in said and "11× as long" in said


def test_what_this_provider_already_holds_counts_as_room(card):
    """Its own loaded models are on the card and its next job reuses them: not an obstacle to itself."""
    assert card(free=900, held=0).device_now(ALIGN) == "cpu"
    assert card(free=900, held=3000).device_now(ALIGN) == "cuda"


def test_the_big_model_counts_as_room_for_the_job_that_reuses_it(card):
    """A `listen` after a `listen` finds its own model loaded: that is room, not an obstacle."""
    from noaap.timing import LISTEN

    engine = card(free=200, held=0)
    engine._whisper, engine._whisper_device = object(), "cuda"

    assert engine.device_now(LISTEN) == "cuda", "3616 MiB of it is loaded already"
    assert engine._whisper is not None, "and it is still loaded afterwards"


def test_a_job_lets_go_of_what_it_will_not_use_before_asking(card):
    """R-284: after a `listen` the process held 3856 MiB of a model an alignment never touches; the
    gate counted it as room and the alignment died half way through. It goes first now."""
    engine = card(free=200, held=0)
    engine._whisper, engine._whisper_device = object(), "cuda"

    where = engine.device_now(ALIGN)

    assert engine._whisper is None, "the big model was let go before the card was asked"
    assert any("letting go of the big model" in said for said in engine.said), engine.said
    # and with only 200 MiB free once it is gone, this alignment honestly belongs on the processor
    assert where == "cpu"


def test_listening_lets_go_of_the_aligner_and_the_separator(card):
    from noaap.timing import LISTEN

    engine = card(free=4000)
    engine._models[("de", "cuda")] = object()
    engine._separator, engine._separator_device = object(), "cuda"

    assert engine.device_now(LISTEN) == "cuda"
    assert engine._models == {} and engine._separator is None
    assert any("the aligner and the separator" in said for said in engine.said), engine.said


def test_a_job_that_finds_only_what_it_uses_says_nothing(card):
    engine = card(free=4000)
    engine._models[("de", "cuda")] = object()

    assert engine.device_now(ALIGN) == "cuda"
    assert engine._models, "the aligner is what an alignment uses: it stays"
    assert engine.said == []


def test_drafting_words_needs_more_room_than_placing_them(card):
    assert card(free=3600).device_now(ALIGN) == "cuda"
    assert card(free=3600).device_now(TRANSCRIBE) == "cpu", "the big model needs 3800"


def test_a_card_that_cannot_be_asked_is_tried_anyway(monkeypatch):
    """A driver or a torch that will not answer is not a reason to spend eleven times the time."""
    engine, *_ = on_a_card(0)
    monkeypatch.setattr("noaap.timing_local.card_room", lambda: (_ for _ in ()).throw(RuntimeError("no")))

    assert engine.device_now(ALIGN) == "cuda"


def test_the_processor_is_never_asked_about_the_card(monkeypatch):
    from noaap.timing_local import LocalTiming

    engine = LocalTiming(device="cpu")
    monkeypatch.setattr("noaap.timing_local.card_room",
                        lambda: (_ for _ in ()).throw(AssertionError("asked about a card it is not using")))
    assert engine.device_now(ALIGN) == "cpu"


def test_running_out_of_memory_half_way_through_is_one_sentence(monkeypatch):
    """No traceback: there is nothing in a stack trace that the person waiting can act on."""
    from noaap.timing_local import CARD_RAN_OUT, LocalTiming

    engine = LocalTiming(device="cuda")
    engine.log = lambda s: None
    engine._models[("de", "cuda")] = object()      # something is loaded, and should be let go
    monkeypatch.setattr(LocalTiming, "_align",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(
                            "CUDA out of memory. Tried to allocate 1.34 GiB")))

    with pytest.raises(TimingUnavailable) as raised:
        engine.align(__import__("pathlib").Path("t.opus"), ["a line"], device="cuda")

    assert str(raised.value) == CARD_RAN_OUT
    assert "ran out of memory" in str(raised.value) and "cpu" in str(raised.value)
    assert engine._models == {}, "what the failed run held goes back for the next attempt"


def test_any_other_failure_is_still_a_failure(monkeypatch):
    from pathlib import Path

    from noaap.timing_local import LocalTiming

    engine = LocalTiming(device="cuda")
    engine.log = lambda s: None
    monkeypatch.setattr(LocalTiming, "_align",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the file is not audio")))

    with pytest.raises(RuntimeError, match="not audio"):
        engine.align(Path("t.opus"), ["a line"], device="cuda")


def test_the_job_log_shows_the_sentence_and_no_traceback(tmp_path):
    """What the page shows a person when a timing job cannot run (item 2)."""
    from noaap.web import Jobs

    jobs = Jobs(lambda job: None)
    job = jobs.submit("align", "Align something",
                      lambda s: (_ for _ in ()).throw(TimingUnavailable("the graphics card ran out of memory")))
    for _ in range(200):
        if job.state in ("failed", "done"):
            break
        __import__("time").sleep(0.01)

    assert job.state == "failed"
    assert job.log == ["the graphics card ran out of memory"]
    assert not any("Traceback" in line for line in job.log)


# -- the real card, only when asked for ------------------------------------------------------------


@pytest.mark.skipif(not config.env("CARD_LIVE"),
                    reason="set NOAAP_CARD_LIVE=1 (and install noaap[timing]) to use the real card")
def test_the_real_card(tmp_path, one_second_of_sound):
    """One alignment on the real card, with the driver's own numbers before, during and after.

    Printed rather than asserted, apart from the two things that must be true: the card holds
    something while the models are loaded, and after letting go it holds no more than the CUDA
    context that belongs to the process until it exits.
    """
    import shutil
    import subprocess

    from noaap.timing_local import LocalTiming

    def driver() -> int:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True)
        mine = [int(row.split(",")[1]) for row in out.stdout.splitlines()
                if row.split(",")[0].strip() == str(os.getpid())]
        return mine[0] if mine else 0

    audio = tmp_path / "t.opus"
    shutil.copy(one_second_of_sound, audio)
    engines = Engines()
    engine = engines.provider(settings(timing_device="cuda", timing_verify=False), ALIGN)
    assert isinstance(engine, LocalTiming)
    engine.log = lambda s: print(s)

    before = driver()
    engine.align(audio, ["hello"], language="en")
    during = driver()
    print(f"driver: {before} MiB before · {during} MiB with the models loaded", end=" ")
    assert during > before, "nothing was loaded at all"

    engines.let_go()
    after = driver()
    print(f"· {after} MiB after letting go")
    assert after < during / 2, "the card was not really given back"
