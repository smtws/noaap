"""Taking another folder into the library from the page (DESIGN §9, slice 92).

The user asked how to configure *"another local or nas folder for merge"*, and the answer was a
command line. These cases hold the page's version to two things: it refuses a folder the pass could
only hurt itself with, and **the check is the apply** — every line `noaap merge` and `noaap adopt`
print in a dry run is a line the page was given, and applying does what that check listed.
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap import adopt as adopt_pass
from noaap import merge as merge_pass
from noaap import sources
from noaap.config import Config
from noaap.download import PLAN_FILE, run
from noaap.plan import build_plan
from noaap.ranking import Facts, Judgement, Verdict
from noaap.service import Service, refuse_folder
from noaap.spectrum import Spectrum
from noaap.web import App

HDR = {"X-Noaap": "1", "Content-Type": "application/json"}


@pytest.fixture
def both(tmp_path, opus_template):
    """The same album twice: one copy is the library, the other is the folder offered to it."""
    library, other = tmp_path / "library", tmp_path / "other"
    plan = build_plan(vol1())
    run(plan, library / plan.folder, FakeYouTube(opus_template))
    run(build_plan(vol1()), other / plan.folder, FakeYouTube(opus_template))
    return library, other


@pytest.fixture
def foreign(both):
    """A collection as somebody else arranged it: the audio, its tags, and no plan anywhere."""
    library, other = both
    outside = library.parent / "somebody-elses"
    for plan_file in other.rglob(PLAN_FILE):
        album = plan_file.parent
        into = outside / album.relative_to(other)
        into.mkdir(parents=True)
        for audio in sorted(album.glob("*.opus")):
            into.joinpath(audio.name).write_bytes(audio.read_bytes())
    return library, outside


def a_better_copy(pair) -> Judgement:
    """What `consider` would say about a copy worth taking, without decoding anything."""
    new = Facts(length=200.0, band=Spectrum(cutoff=22, full=True, why="at this file's ceiling"),
                codec="flac", bitrate=900_000, bytes=9_000_000, lossless=True)
    old = Facts(length=200.0, band=Spectrum(cutoff=20, why="band-limited"), codec="opus",
                bitrate=128_000, bytes=1_000_000)
    return Judgement(Verdict.REPLACE, "a better copy", new, old)


@pytest.fixture
def judged(monkeypatch):
    """Every pair is worth taking, so an apply really acts — the decoding is not what is under test.

    Returns the pass's own survey, judged the same way, so a case can ask what the command would print.
    """
    real = merge_pass.survey
    monkeypatch.setattr("noaap.merge.survey",
                        lambda source, target, **kw: real(source, target, judge=a_better_copy, **kw))
    return lambda source, target: real(source, target, judge=a_better_copy)


def service(library, log):
    return Service(Config(musicbrainz=False, library_root=library), library, log=log.append)


# -- a folder it will not take ---------------------------------------------------------------------


def test_the_library_is_not_taken_into_itself(tmp_path):
    library = tmp_path / "library"
    (library / "A Band" / "Album").mkdir(parents=True)
    assert refuse_folder(library, library) == "that is the library itself"
    assert "inside the library" in refuse_folder(library / "A Band", library)
    assert "holds the library" in refuse_folder(tmp_path, library)


def test_a_path_that_is_no_folder_is_refused_with_a_sentence(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    a_file = tmp_path / "notes.txt"
    a_file.write_text("not a folder")
    assert refuse_folder(a_file, library) == f"{a_file} is a file, not a folder"
    assert refuse_folder(tmp_path / "nowhere", library) == f"there is nothing at {tmp_path / 'nowhere'}"
    assert "not an absolute path" in refuse_folder(type(library)("music"), library)
    assert refuse_folder(type(library)(""), library) == "name a folder to take in"
    assert refuse_folder(tmp_path / "elsewhere", library) != ""


def test_a_folder_that_can_be_taken_in_is_not_refused(both):
    library, other = both
    assert refuse_folder(other, library) == ""


def test_the_service_refuses_it_too_and_writes_nothing(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    log: list[str] = []
    outcome = service(library, log).take_in(library, "merge", dry_run=False)
    assert outcome.status == "failed"
    assert outcome.message == "that is the library itself"
    assert list(library.iterdir()) == []


def test_a_way_of_taking_it_in_that_does_not_exist(both):
    library, other = both
    outcome = service(library, []).take_in(other, "sideways", dry_run=True)
    assert outcome.status == "failed"
    assert "sideways" in outcome.message


# -- the check is what the command prints ----------------------------------------------------------


def test_a_merge_check_holds_every_line_the_command_prints(both, judged):
    library, other = both
    log: list[str] = []
    outcome = service(library, log).take_in(other, "merge", dry_run=True)
    assert outcome.status == "ok"

    printed = merge_pass.report(judged(other, library), applying=False)
    assert printed, "the pass had nothing to say, so this case would prove nothing"
    missing = [line for line in printed if line not in log]
    assert missing == []


def test_an_adopt_check_holds_every_line_the_command_prints(foreign):
    library, other = foreign
    log: list[str] = []
    outcome = service(library, log).take_in(other, "adopt", dry_run=True)
    assert outcome.status == "ok"

    printed = adopt_pass.report(
        adopt_pass.survey(other, other, sources.get("folder", Config(musicbrainz=False))),
        applying=False)
    assert printed
    assert [line for line in printed if line not in log] == []


def test_a_check_writes_nothing_on_either_side(both, foreign, judged):
    library, other = both
    _, outside = foreign
    before = {p: p.stat().st_mtime_ns for p in sorted(library.rglob("*")) if p.is_file()}
    others = {p: p.stat().st_mtime_ns for p in sorted(other.rglob("*")) if p.is_file()}

    outside_before = {p: p.stat().st_mtime_ns for p in sorted(outside.rglob("*")) if p.is_file()}
    assert service(library, []).take_in(other, "merge", dry_run=True).status == "ok"
    assert service(library, []).take_in(outside, "adopt", dry_run=True).status == "ok"

    assert {p: p.stat().st_mtime_ns for p in sorted(library.rglob("*")) if p.is_file()} == before
    assert {p: p.stat().st_mtime_ns for p in sorted(other.rglob("*")) if p.is_file()} == others
    assert {p: p.stat().st_mtime_ns for p in sorted(outside.rglob("*")) if p.is_file()} == outside_before


# -- and the apply does exactly that ---------------------------------------------------------------


def described(lines: list[str]) -> list[str]:
    """The part of a report that is about the music: everything before its counting."""
    return [line for line in lines[:next(n for n, line in enumerate(lines) if " to replace, " in line)]
            if line.strip()]


def test_applying_a_merge_does_what_the_check_listed(both, judged):
    library, other = both
    checked: list[str] = []
    service(library, checked).take_in(other, "merge", dry_run=True)
    applied: list[str] = []
    outcome = service(library, applied).take_in(other, "merge", dry_run=False)

    assert outcome.status == "ok"
    # the same albums and the same tracks, named the same way
    assert described(checked) == described(applied)
    # what it listed is what it did: one replacement per pair, and the displaced files are in the bin
    took = len([line for line in checked if "a better copy" in line])
    assert took and f"{took} replaced, 0 filled" in outcome.message
    assert f"{took} replaced" in applied[-1]


def test_applying_an_adopt_writes_one_plan_per_album_and_nothing_else(foreign):
    library, other = foreign
    checked: list[str] = []
    service(library, checked).take_in(other, "adopt", dry_run=True)
    before = {p for p in other.rglob("*") if p.is_file()}

    outcome = service(library, []).take_in(other, "adopt", dry_run=False)

    assert outcome.status == "ok"
    written = {p for p in other.rglob("*") if p.is_file()} - before
    assert written == {p / PLAN_FILE for p in other.glob("*/*")}
    assert f"{len(written)} album(s) adopted" == outcome.message
    assert f"{len(written)} album folder(s) under {other.name}" in checked[0]


# -- and the same thing through the door -----------------------------------------------------------


@pytest.fixture
def server(both, opus_template):
    library, other = both
    yt = FakeYouTube(opus_template)
    app = App(Config(musicbrainz=False, library_root=library), library, port=0,
              service_factory=lambda job: Service(Config(musicbrainz=False, library_root=library),
                                                  library, log=job.log.append, yt=yt))
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=20) as client:
        yield client, library, other
    srv.shutdown()


def wait(client, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/job?id={job_id}").json()
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("the job never settled")


def test_the_door_refuses_a_folder_inside_the_library_with_a_sentence(server):
    client, library, _ = server
    r = client.post("/api/take_in", json={"folder": str(library), "mode": "merge", "dry_run": True},
                    headers=HDR)
    assert r.status_code == 400
    assert r.json()["error"] == "that is the library itself"

    r = client.post("/api/take_in", json={"folder": "music", "mode": "merge", "dry_run": True}, headers=HDR)
    assert r.status_code == 400
    assert "not an absolute path" in r.json()["error"]


def test_the_door_refuses_a_way_of_taking_it_in_that_does_not_exist(server):
    client, _, other = server
    r = client.post("/api/take_in", json={"folder": str(other), "mode": "sideways"}, headers=HDR)
    assert r.status_code == 400
    assert "sideways" in r.json()["error"]


def test_a_check_through_the_door_is_a_read_and_says_what_it_would_do(server, judged):
    client, library, other = server
    r = client.post("/api/take_in", json={"folder": str(other), "mode": "merge", "dry_run": True},
                    headers=HDR)
    assert r.status_code == 202
    job = wait(client, r.json()["job"]["id"])
    assert job["state"] == "done"
    assert "Check what taking in" in job["label"]
    assert any("nothing was changed" in line for line in job["log"])
    # a read runs beside anything else: it is not the writing lane
    assert client.get("/api/state").json()["busy_write"] is False
