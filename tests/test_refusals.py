"""A refused write leaves one file, never the pass (DESIGN §9, slice 112; R-433, ruling 3).

`precautions.safely` writes to a copy beside the file, proves the recording survived, and replaces
the file in one step; when the proof fails it raises `Unsafe` and the file that was there is still
there. That much was right. What was wrong was what happened next: the exception reached nobody, so
the pass over the user's collection ended in a traceback an hour in, with 47 GB copied, five batches
unrun, and one file as its only subject.
"""

from __future__ import annotations

import itertools

import pytest
from test_collisions import a_service
from test_id3v1_tail import their_album
from test_intake import QUIET

from noaap import intake, precautions
from noaap.download import load_plan
from noaap.tag import _id3v1_tail

# -- a refused write leaves its one file (R-433, ruling 3) -------------------------------------


def refusing(monkeypatch, name):
    """Make the precaution say no to one file and tell the truth about every other.

    Both halves of the proof are stubbed, because either alone would let the write through: the
    snapshot's expectation is compared with a digest of the copy, so the fake digest has to differ
    from itself — a snapshot that recorded the same fake would match it and the write would pass.
    """
    same, sha, counter = precautions.same_audio, precautions.decoded_sha, itertools.count()
    monkeypatch.setattr(precautions, "same_audio",
                        lambda a, b: False if name in a.name else same(a, b))
    monkeypatch.setattr(precautions, "decoded_sha",
                        lambda p: f"not a digest {next(counter)}" if name in p.name else sha(p))


@pytest.fixture
def three(tmp_path, one_second_of_mp3):
    root = tmp_path / "collection"
    return root, their_album(root, ["One", "Two", "Three"], one_second_of_mp3)


def test_one_unsafe_file_in_three_leaves_that_file_and_the_pass_goes_on(three, monkeypatch):
    root, where = three
    refusing(monkeypatch, "One")

    said = []
    done = intake.take_in(a_service(root), root, QUIET, dry_run=False, log=said.append)

    assert len(done.left_untouched) == 1, done.left_untouched
    named, why = done.left_untouched[0]
    assert "One" in named
    assert "not the same recording" in why and "nothing was changed" in why
    assert "left untouched — 1 file(s):" in said, said
    assert [line for line in said if line.strip() == f"{named} — {why}"], said
    assert done.adopted == 1 and done.tracks == 3, "the album was still taken in"
    assert done.retagged == 2, "and the other two files were written"
    assert not done.stopped


def test_the_file_a_refusal_left_is_byte_for_byte_what_it_was(three, monkeypatch):
    root, where = three
    before = {p.name: p.read_bytes() for p in where.glob("*.mp3")}
    refusing(monkeypatch, "One")

    intake.take_in(a_service(root), root, QUIET, dry_run=False, log=lambda s: None)

    left = next(p for p in where.glob("*.mp3") if "One" in p.name)
    assert left.read_bytes() == before["01 One.mp3"]
    assert _id3v1_tail(left) is not None, "its tail is still there, because nothing was written"
    written = [p for p in where.glob("*.mp3") if "One" not in p.name]
    assert len(written) == 2 and all(_id3v1_tail(p) is None for p in written)


def test_the_plan_records_why_so_the_next_pass_can_be_told(three, monkeypatch):
    root, where = three
    refusing(monkeypatch, "One")

    intake.take_in(a_service(root), root, QUIET, dry_run=False, log=lambda s: None)

    plan = load_plan(where)
    assert plan is not None
    refused = [t for t in plan.tracks if t.error]
    assert len(refused) == 1
    assert "not the same recording" in refused[0].error
    assert refused[0].tagged is None, "and it is not recorded as tagged, so a later pass retries"


def test_without_the_guard_the_whole_pass_ends_in_the_traceback(three, monkeypatch):
    """The stop of 2026-10-03, reproduced: one file, and five batches that never run.

    Kept as a case because the handler is three lines and the thing it prevents is eight hours of a
    run that had already copied 47 GB.
    """
    root, where = three
    refusing(monkeypatch, "One")
    import noaap.download as download

    monkeypatch.setattr(download, "Unsafe", type("NotCaught", (Exception,), {}))

    with pytest.raises(precautions.Unsafe, match="not the same recording"):
        intake.take_in(a_service(root), root, QUIET, dry_run=False, log=lambda s: None)
