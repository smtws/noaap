"""What the server ran is on record after the fact (DESIGN §9, slice 184; P132, R-589 item 2).

A timing job that ran on the processor because the card had no room, or that lost the card half way
through, said so in its own job log and nowhere else: the service logs at WARNING, and the jobs
lived in memory, so after a restart nothing said what had run.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from test_listen import a_library, a_service

from noaap.timing import ALIGN, TRANSCRIBE, Timed, TimedLine, TimingUnavailable

ROOM = "the graphics card has 900 MiB free of 8188 and this needs about 3500"


class Hands:
    """A provider that runs here and says where, and how long it takes."""

    name = "local"

    def __init__(self, device="cuda", why=None, fails=None):
        self.device, self.why, self.fails = device, why, fails

    def capabilities(self):
        return frozenset({ALIGN, TRANSCRIBE})

    def device_and_reason(self, what=ALIGN):
        return self.device, self.why

    def device_now(self, what=ALIGN):
        return self.device

    def align(self, audio, lines, *, language=None, check=None, device=None):
        if self.fails:
            raise self.fails
        return Timed([TimedLine(line, 1.0) for line in lines], provider="local", model="m")


def card_lines(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "noaap.service"]


def aligning(monkeypatch, tmp_path, caplog, engine):
    monkeypatch.setattr("noaap.timing.provider", lambda _cfg, _what="": engine)
    _, plan = a_library(tmp_path)
    service, _ = a_service(tmp_path)
    caplog.set_level(logging.WARNING, logger="noaap.service")
    return service, plan


# -- the journal: one WARNING line per album ----------------------------------------------------------


def test_a_job_moved_to_the_processor_is_one_warning_with_the_album_device_reason_and_time(
        monkeypatch, tmp_path, caplog):
    service, plan = aligning(monkeypatch, tmp_path, caplog, Hands(device="cpu", why=ROOM))

    service.align_lyrics(plan.source_id, "v1", "a line\n")

    [line] = card_lines(caplog)
    said = line.getMessage()
    assert said.startswith("timing ran on the processor, not the card: Somebody — An Album, 01 A Song (align)"), said
    assert "device cpu" in said and ROOM in said, said
    assert said.rstrip().endswith(" s") and any(c.isdigit() for c in said.rsplit("·", 1)[1]), said


def test_a_job_that_lost_the_card_is_one_warning_too(monkeypatch, tmp_path, caplog):
    from noaap.timing_local import CARD_RAN_OUT

    service, plan = aligning(monkeypatch, tmp_path, caplog, Hands(fails=TimingUnavailable(CARD_RAN_OUT)))

    with pytest.raises(TimingUnavailable):
        service.align_lyrics(plan.source_id, "v1", "a line\n")

    [line] = card_lines(caplog)
    said = line.getMessage()
    assert said.startswith("timing lost the card part way through: Somebody — An Album, 01 A Song (align)"), said
    assert "device cuda" in said and "out of memory" in said, said


@pytest.mark.parametrize("engine", [Hands(device="cuda"), Hands(device="cpu", why=None)],
                         ids=["on the card", "the processor by choice"])
def test_a_job_where_it_was_meant_to_run_says_nothing_to_the_journal(monkeypatch, tmp_path, caplog, engine):
    service, plan = aligning(monkeypatch, tmp_path, caplog, engine)

    service.align_lyrics(plan.source_id, "v1", "a line\n")

    assert card_lines(caplog) == []


def test_any_other_failure_is_not_a_card_line(monkeypatch, tmp_path, caplog):
    service, plan = aligning(monkeypatch, tmp_path, caplog,
                             Hands(fails=TimingUnavailable("there are no words to place")))

    with pytest.raises(TimingUnavailable):
        service.align_lyrics(plan.source_id, "v1", "a line\n")

    assert card_lines(caplog) == []


def test_the_local_provider_says_why_it_left_the_card(monkeypatch):
    from test_card import on_a_card

    engine, *_ = on_a_card(900)
    monkeypatch.setattr("noaap.timing_local.card_room", lambda: (900, 8188))
    monkeypatch.setattr("noaap.timing_local.pool_held", lambda: 0)

    device, why = engine.device_and_reason(ALIGN)

    assert device == "cpu" and "900 MiB free of 8188" in why and "3500" in why
    assert engine.device_now(ALIGN) == "cpu", "the old question is the first half of the new one"


def test_the_local_provider_has_no_reason_where_it_stays(monkeypatch):
    from noaap.timing_local import LocalTiming

    assert LocalTiming(device="cpu").device_and_reason(ALIGN) == ("cpu", None)


# -- the job table on disk ---------------------------------------------------------------------------


def settled(job, seconds=5.0):
    end = time.monotonic() + seconds
    while job.state in ("queued", "running") and time.monotonic() < end:
        time.sleep(0.01)
    return job


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def a_table(tmp_path):
    from noaap.jobtable import JobTable

    return JobTable(tmp_path / "state" / "jobs.jsonl")


def test_every_job_is_a_start_and_an_end_on_disk(tmp_path):
    from noaap.web import Jobs

    table = a_table(tmp_path)
    jobs = Jobs(lambda job: None, table=table)
    job = settled(jobs.submit("update", "Update A — B", lambda s: None, target="an-album"))

    start, end = records(table.path)
    assert start["event"] == "start" and end["event"] == "end"
    for r in (start, end):
        assert r["id"] == job.id and r["kind"] == "update" and r["album"] == "an-album"
        assert r["label"] == "Update A — B" and r["run"] == table.run and r["ts"]
    assert end["outcome"] == "done" and end["seconds"] >= 0


def test_a_failed_job_says_why_in_its_record(tmp_path):
    from noaap.web import Jobs

    table = a_table(tmp_path)
    jobs = Jobs(lambda job: None, table=table)
    settled(jobs.submit("align", "Align a song",
                        lambda s: (_ for _ in ()).throw(TimingUnavailable("the card ran out"))))

    end = records(table.path)[-1]
    assert end["outcome"] == "failed" and end["why"] == "the card ran out" and end["album"] is None


def test_a_job_cancelled_before_it_ran_has_only_an_end(tmp_path):
    from noaap.web import Jobs

    table = a_table(tmp_path)
    jobs = Jobs(lambda job: None, table=table)
    hold = threading.Event()
    first = jobs.submit("update", "first", lambda s: hold.wait(5))
    second = jobs.submit("update", "second", lambda s: None)
    jobs.cancel(second.id)
    hold.set()
    settled(first)

    mine = [r for r in records(table.path) if r["id"] == second.id]
    assert [(r["event"], r.get("outcome")) for r in mine] == [("end", "cancelled")]


def test_a_restart_closes_what_the_last_server_left_open(tmp_path):
    from noaap.jobtable import JobTable

    path = tmp_path / "jobs.jsonl"
    before = JobTable(path, run="earlier-1")
    before.started(7, "update", "Update the library", None, "write")
    before.started(8, "align", "Align a song", "an-album", "read")
    before.ended(8, "align", "Align a song", "an-album", "read", "done", 1.5)

    now = JobTable(path, run="now-2")

    closed = [r for r in records(path) if r["run"] == "earlier-1" and r.get("outcome") == "interrupted"]
    assert [(r["id"], r["kind"], r["event"]) for r in closed] == [(7, "update", "end")]
    assert "seconds" not in closed[0] or closed[0]["seconds"] is None
    JobTable(path, run="later-3")
    assert len([r for r in records(path) if r.get("outcome") == "interrupted"]) == 1, "closed once"
    assert now.run == "now-2"


def test_the_table_rotates_past_its_size(tmp_path):
    from noaap import jobtable

    table = jobtable.JobTable(tmp_path / "jobs.jsonl")
    table.path.write_text("x" * (jobtable.ROTATE_AT + 1))

    table.started(1, "update", "u", None, "write")

    assert (tmp_path / "jobs.jsonl.1").stat().st_size == jobtable.ROTATE_AT + 1
    assert len(records(table.path)) == 1


def test_a_table_that_cannot_be_written_never_fails_the_job(tmp_path, caplog):
    from noaap.jobtable import JobTable
    from noaap.web import Jobs

    blocked = tmp_path / "not-a-dir"
    blocked.write_text("a file where the directory should be")
    jobs = Jobs(lambda job: None, table=JobTable(blocked / "jobs.jsonl"))
    caplog.set_level(logging.WARNING)

    job = settled(jobs.submit("update", "u", lambda s: None))

    assert job.state == "done"
    assert any("job table" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_the_state_dir_follows_xdg(monkeypatch, tmp_path):
    from noaap import config

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert config.state_dir() == tmp_path / "noaap"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert str(config.state_dir()).endswith(".local/state/noaap")


def test_the_server_keeps_its_table_in_the_state_dir(monkeypatch, tmp_path):
    from noaap.config import Config
    from noaap.web import App

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    app = App(Config(musicbrainz=False, timing_card_idle_seconds=0), tmp_path / "lib")

    assert app.jobs.table.path == tmp_path / "state" / "noaap" / "jobs.jsonl"
