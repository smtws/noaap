"""The plan on disk is the only state (DESIGN.md §6), so a refactor has to prove it still reads and
writes it exactly as v0.7.0 did.

`tests/fixtures/plans/` holds one real plan per shape the reference library contains, plus two built
ones for shapes it does not. Every case here is about the *format*: load, save, and whether anything
was lost on the way.

Byte-identity is deliberately **not** the invariant. A plan written before a field existed omits it,
and writing it back fills it in with its default — measured over the reference library: 246 plans,
140 byte-identical, 106 differing, and **every difference additive**. Nothing was ever removed or
changed. So the invariant is: *no key disappears and no value changes*, and round-tripping twice is
byte-stable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ytalbum.download import load_plan, save_plan
from ytalbum.models import AlbumPlan

PLANS = sorted((Path(__file__).parent / "fixtures" / "plans").glob("*.json"))
assert PLANS, "the plan fixtures are missing"


def raw(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def serialise(plan: AlbumPlan) -> str:
    """Exactly what `save_plan` writes, without touching a disk."""
    return json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n"


def differences(before: Any, after: Any, path: str = "") -> tuple[list[str], list[str]]:
    """(keys that vanished, values that changed) — additions are allowed and not reported."""
    gone: list[str] = []
    changed: list[str] = []
    if isinstance(before, dict) and isinstance(after, dict):
        gone += [f"{path}{k}" for k in before.keys() - after.keys()]
        for k in before.keys() & after.keys():
            g, c = differences(before[k], after[k], f"{path}{k}.")
            gone += g
            changed += c
    elif isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            changed.append(f"{path}length {len(before)} -> {len(after)}")
        for i, (b, a) in enumerate(zip(before, after, strict=False)):
            g, c = differences(b, a, f"{path}{i}.")
            gone += g
            changed += c
    elif before != after:
        changed.append(f"{path}{before!r} -> {after!r}")
    return gone, changed


# `candidates_stale` is the one plan where a value is *meant* to change: it carries a `chosen` that
# disagrees with its own `source_override`, and loading repairs it. That is the whole point of the
# fixture, and it has a case of its own below.
REPAIRED = {"candidates_stale"}


@pytest.mark.parametrize("path", [p for p in PLANS if p.stem not in REPAIRED], ids=lambda p: p.stem)
def test_no_field_is_lost_and_no_value_changes(path: Path) -> None:
    before = raw(path)
    after = AlbumPlan.from_dict(before).to_dict()
    gone, changed = differences(before, after)
    assert not gone, f"{path.name} lost {gone}"
    assert not changed, f"{path.name} changed {changed}"


@pytest.mark.parametrize("path", PLANS, ids=lambda p: p.stem)
def test_a_second_round_trip_is_byte_identical(path: Path) -> None:
    """Once the defaults are filled in, the format is a fixed point — which is what lets a later
    version diff two plans and see only what actually changed."""
    once = serialise(AlbumPlan.from_dict(raw(path)))
    twice = serialise(AlbumPlan.from_dict(json.loads(once)))
    assert once == twice


@pytest.mark.parametrize("path", PLANS, ids=lambda p: p.stem)
def test_every_value_keeps_its_type(path: Path) -> None:
    before = raw(path)
    after = AlbumPlan.from_dict(before).to_dict()

    def walk(b: Any, a: Any, where: str) -> None:
        assert type(b) is type(a), f"{where}: {type(b).__name__} became {type(a).__name__}"
        if isinstance(b, dict):
            for k in b.keys() & a.keys():
                walk(b[k], a[k], f"{where}{k}.")
        elif isinstance(b, list):
            for i, (x, y) in enumerate(zip(b, a, strict=False)):
                walk(x, y, f"{where}{i}.")

    walk(before, after, "")


@pytest.mark.parametrize("path", PLANS, ids=lambda p: p.stem)
def test_a_plan_survives_the_disk(path: Path, tmp_path: Path) -> None:
    """`save_plan` then `load_plan`, for real, because atomic replace and encoding are part of it."""
    plan = AlbumPlan.from_dict(raw(path))
    save_plan(plan, tmp_path)
    back = load_plan(tmp_path)
    assert back is not None
    assert back.to_dict() == plan.to_dict()


def test_a_plan_from_a_later_version_survives_unchanged() -> None:
    """Forward compatibility, and it is not theoretical: the desktop app and a terminal share one
    library, so a plan written by the newer ytalbum is read by the older every time it runs.

    Before this, an unknown key raised `TypeError` on load. Dropping it instead would have been
    worse — the newer ytalbum would silently lose a field it had written and believed saved.
    """
    path = next(p for p in PLANS if p.stem == "from_a_later_version")
    before = raw(path)
    assert "future_album_field" in before, "the fixture no longer carries an unknown field"

    after = AlbumPlan.from_dict(before).to_dict()
    assert after["future_album_field"] == before["future_album_field"]
    assert after["tracks"][0]["future_track_field"] == before["tracks"][0]["future_track_field"]
    gone, changed = differences(before, after)
    assert not gone and not changed

    # and the marker the implementation uses never reaches the file
    assert "_unknown_fields" not in serialise(AlbumPlan.from_dict(before))


def test_an_unknown_schema_is_still_refused() -> None:
    """Tolerating unknown *fields* must not become tolerating an unknown *format*."""
    before = raw(PLANS[0]) | {"schema": 999}
    with pytest.raises(ValueError, match="unsupported plan schema"):
        AlbumPlan.from_dict(before)


def test_the_fixtures_cover_every_shape_worth_covering() -> None:
    """If a shape stops being represented, this says so rather than quietly testing less."""
    plans = [raw(p) for p in PLANS]

    def any_track(d: dict[str, Any], f: Any) -> bool:
        return any(f(t) for t in d["tracks"])

    def fit(d: dict[str, Any], verdict: str) -> bool:
        return any_track(d, lambda t: (t.get("lyrics_fit") or {}).get("decided") == verdict)

    shapes = {
        "official_album": lambda d: d["kind"] == "official_album",
        "compilation": lambda d: d["kind"] == "compilation",
        "single": lambda d: d["kind"] == "single",
        "artist_playlist": lambda d: d["kind"] == "artist_playlist",
        "trimmed": lambda d: any_track(d, lambda t: t.get("trim_start") or t.get("trim_end")),
        "source_override": lambda d: any_track(d, lambda t: t.get("source_override")),
        "user lyrics": lambda d: any_track(d, lambda t: (t.get("provenance") or {}).get("lyrics") == "user"),
        "lyrics_rejected": lambda d: any_track(d, lambda t: t.get("lyrics_rejected")),
        "fit words+stamps": lambda d: fit(d, "words+stamps"),
        "fit words": lambda d: fit(d, "words"),
        "fit reject": lambda d: fit(d, "reject"),
        "fit unclear": lambda d: fit(d, "unclear"),
        "fit shown": lambda d: fit(d, "shown"),
        "lyrics_no_entry": lambda d: any_track(d, lambda t: t.get("lyrics_no_entry")),
        "lyrics_timed_by": lambda d: any_track(d, lambda t: t.get("lyrics_timed_by")),
        "lyrics_published": lambda d: any_track(d, lambda t: t.get("lyrics_published")),
        "no_audio_stream": lambda d: any_track(d, lambda t: t.get("error_kind") == "no_audio_stream"),
        "failed": lambda d: any_track(d, lambda t: t.get("state") == "failed"),
        "multi-disc": lambda d: len({t.get("disc") or 1 for t in d["tracks"]}) > 1,
        "unknown fields": lambda d: "future_album_field" in d,
    }
    missing = [name for name, test in shapes.items() if not any(test(d) for d in plans)]
    assert not missing, f"no fixture covers {missing}"


# -- ytalbum plan --verify -------------------------------------------------------------------------


def test_verify_reports_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ytalbum.cli import main

    library = tmp_path / "lib"
    for i, path in enumerate(PLANS[:3]):
        folder = library / f"artist{i}" / f"album{i}"
        folder.mkdir(parents=True)
        (folder / ".ytalbum.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    before = {p: p.read_bytes() for p in library.rglob(".ytalbum.json")}

    assert main(["plan", "--verify", "--library", str(library)]) == 0
    said = capsys.readouterr().out
    assert "3 plan(s):" in said
    assert "0 would lose or change something" in said
    assert {p: p.read_bytes() for p in library.rglob(".ytalbum.json")} == before, "--verify wrote"


def test_verify_names_an_unknown_field_and_still_passes(tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    from ytalbum.cli import main

    folder = tmp_path / "lib" / "a" / "b"
    folder.mkdir(parents=True)
    source = next(p for p in PLANS if p.stem == "from_a_later_version")
    (folder / ".ytalbum.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["plan", "--verify", "--library", str(tmp_path / "lib")]) == 0
    said = capsys.readouterr().out
    assert "unknown field 'future_album_field'" in said
    assert "unknown field 'tracks.future_track_field'" in said
    assert "carried through" in said


def test_verify_fails_loudly_on_a_plan_it_cannot_read(tmp_path: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    from ytalbum.cli import main

    folder = tmp_path / "lib" / "a" / "b"
    folder.mkdir(parents=True)
    broken = raw(PLANS[0]) | {"schema": 999}
    (folder / ".ytalbum.json").write_text(json.dumps(broken), encoding="utf-8")

    assert main(["plan", "--verify", "--library", str(tmp_path / "lib")]) == 1
    assert "cannot be read" in capsys.readouterr().out


def test_plan_without_a_url_or_verify_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    from ytalbum.cli import main

    assert main(["plan"]) == 2
    assert "--verify" in capsys.readouterr().err


def test_the_old_fields_win_when_a_plan_disagrees_with_itself() -> None:
    """`candidates`/`chosen` are derived; `video_id`/`source_override` are the truth (slice 50).

    Two ytalbums share a library and an older one writes `source_override` knowing nothing about
    candidates — so a plan whose `chosen` disagrees with its own override is repaired on load, in
    the direction of the field the older version can still write.
    """
    path = next(p for p in PLANS if p.stem == "candidates_stale")
    before = raw(path)
    track = before["tracks"][0]
    assert track["chosen"] != track["source_override"], "the fixture no longer disagrees"

    after = AlbumPlan.from_dict(before).to_dict()
    repaired = after["tracks"][0]
    assert repaired["chosen"] == track["source_override"]
    assert track["source_override"] in [c["ref"] for c in repaired["candidates"]]
    # the override was missing from the list and is added, which is the other half of the repair
    assert len(repaired["candidates"]) == len(track["candidates"]) + 1

    # and nothing else moved: no key lost, and no value changed outside those two
    gone, changed = differences(before, after)
    assert not gone
    mine = ("tracks.0.chosen", "tracks.0.candidates")
    assert [c for c in changed if not c.startswith(mine)] == [], changed
