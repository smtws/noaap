"""The 1.0.0 promise, kept against a reader that does not know this version (DESIGN §9, slice 62).

`noaap` promises that a plan is additive: a key is never removed, a value never rewritten by a newer
version, and a key a newer version wrote is carried through untouched by an older one. **The promise
was broken from 1.1.0.** ytalbum 0.9.1 builds a candidate with `Candidate(**c)` — a bare constructor —
so every field noaap added to `Candidate` after slice 50 made the plan unreadable to it:
`Candidate.__init__() got an unexpected keyword argument 'length_by'`, on 153 of the 329 plans of the
real library. Slice 48's pass-through carries unknown keys on the album and on the track, and a
candidate is neither.

So the ten fields 0.9.1 knows are written inside the candidate and the rest beside it, at track level,
keyed by the ref — where 0.9.1's `keeping()` carries them through whole.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from noaap.download import CANDIDATE_1_0, COPIES_EXTRA, PLAN_FILE, load_plan, save_plan
from noaap.models import AlbumPlan, Candidate, Kind, PlanTrack

# -- ytalbum 0.9.1, as far as a plan is concerned --------------------------------------------------
#
# Copied from `src/ytalbum/models.py` at 4732f00 ("0.9.1: the last ytalbum"): the ten fields of its
# `Candidate`, its `keeping`/`kept` pass-through, and the bare `Candidate(**c)` that refuses anything
# else. **This is the reader the promise is made to**, and it is here so the writer cannot drift away
# from it again without a case failing.

OLD_CANDIDATE_FIELDS = ("ref", "provider", "length", "codec", "bitrate", "sample_rate", "channels",
                        "added_by", "why", "when")


@dataclass
class OldCandidate:
    ref: str
    provider: str = "youtube"
    length: float | None = None
    codec: str | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    added_by: str = "source"
    why: str = ""
    when: str = ""


@dataclass
class OldTrack:
    video_id: str
    number: int = 1
    artist: str = ""
    title: str = ""
    filename: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    candidates: list[OldCandidate] = field(default_factory=list)
    kept: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OldTrack:
        known = {f.name for f in fields(cls)} - {"kept"}
        made = cls(**{k: v for k, v in d.items() if k in known and k != "candidates"},
                   candidates=[OldCandidate(**c) for c in d.get("candidates") or []])
        made.kept = {k: v for k, v in d.items() if k not in known}
        return made

    def to_dict(self) -> dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "kept"}
        out["candidates"] = [c.__dict__ for c in self.candidates]
        return {**out, **self.kept}


def read_as_0_9_1(path: Path) -> tuple[dict[str, Any], list[OldTrack]]:
    """What 0.9.1 makes of this file. Raises exactly as it does when it cannot."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError(f"unsupported plan schema {data.get('schema')!r} (expected 1)")
    return data, [OldTrack.from_dict(t) for t in data["tracks"]]


def write_as_0_9_1(path: Path, data: dict[str, Any], tracks: list[OldTrack]) -> None:
    path.write_text(json.dumps({**data, "tracks": [t.to_dict() for t in tracks]},
                               indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# -- the guard ------------------------------------------------------------------------------------


def test_the_ten_fields_are_the_ones_that_reader_has():
    """If `CANDIDATE_1_0` ever drifts from 0.9.1's class, this is the case that says so."""
    assert CANDIDATE_1_0 == OLD_CANDIDATE_FIELDS


def test_a_field_added_to_candidate_goes_beside_it_on_its_own():
    """Nothing has to remember to add it: the compatible set is named, the rest is derived."""
    added_since = [f.name for f in fields(Candidate) if f.name not in CANDIDATE_1_0]

    assert added_since, "there are fields 0.9.1 does not know, or this case has nothing to guard"
    assert set(added_since).isdisjoint(CANDIDATE_1_0)


# -- and what the writer actually writes ----------------------------------------------------------


def a_plan(album_dir: Path) -> AlbumPlan:
    track = PlanTrack(video_id="aaaaaaaaaaa", number=1, artist="A Band", title="One",
                      filename="01 - One.opus", provenance={}, state="done")
    track.candidates = [
        Candidate(ref="aaaaaaaaaaa", added_by="source", length=200.0, codec="opus",
                  cutoff_khz=20, full_band=False, bytes=3_000_000, stream_sha="deadbeef"),
        Candidate(ref="bbbbbbbbbbb", added_by="pass", undecided=True, why="nobody could rank it",
                  length_by="decoded", cutoff_khz=22, full_band=True),
    ]
    track.chosen = "aaaaaaaaaaa"
    plan = AlbumPlan(source_url="https://y/1", source_id="p1", kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=None,
                     folder="A Band/An Album", tracks=[track])
    save_plan(plan, album_dir)
    return plan


def test_0_9_1_reads_a_plan_this_version_wrote(tmp_path):
    a_plan(tmp_path / "A Band" / "An Album")

    data, tracks = read_as_0_9_1(tmp_path / "A Band" / "An Album" / PLAN_FILE)

    assert [c.ref for c in tracks[0].candidates] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert tracks[0].candidates[0].codec == "opus", "the fields it knows are where it looks"
    assert COPIES_EXTRA in tracks[0].kept, "and the rest is where it carries things through"


def test_and_writes_it_back_without_losing_what_it_does_not_know(tmp_path):
    """The other half of the promise, and the half that is easy to get wrong."""
    album_dir = tmp_path / "A Band" / "An Album"
    a_plan(album_dir)
    before = load_plan(album_dir)

    data, tracks = read_as_0_9_1(album_dir / PLAN_FILE)
    write_as_0_9_1(album_dir / PLAN_FILE, data, tracks)   # 0.9.1 saves the album
    after = load_plan(album_dir)

    assert after.to_dict() == before.to_dict()
    kept = after.tracks[0].candidate("bbbbbbbbbbb")
    assert kept is not None and kept.undecided and kept.cutoff_khz == 22 and kept.length_by == "decoded"


def test_a_copy_0_9_1_removed_does_not_come_back(tmp_path):
    """It carries the key through whole, including an entry for a copy it dropped. The next save
    writes only what is still there — the legacy fields stay the truth, as they have since slice 50."""
    album_dir = tmp_path / "A Band" / "An Album"
    a_plan(album_dir)

    data, tracks = read_as_0_9_1(album_dir / PLAN_FILE)
    tracks[0].candidates = [c for c in tracks[0].candidates if c.ref != "bbbbbbbbbbb"]
    write_as_0_9_1(album_dir / PLAN_FILE, data, tracks)
    plan = load_plan(album_dir)
    save_plan(plan, album_dir)

    written = json.loads((album_dir / PLAN_FILE).read_text())
    assert [c["ref"] for c in written["tracks"][0]["candidates"]] == ["aaaaaaaaaaa"]
    assert "bbbbbbbbbbb" not in (written["tracks"][0].get(COPIES_EXTRA) or {})


def test_the_old_reader_refuses_what_1_5_0_wrote(tmp_path):
    """The defect itself, so that the case fails if the writer ever goes back to it."""
    album_dir = tmp_path / "A Band" / "An Album"
    a_plan(album_dir)
    data = json.loads((album_dir / PLAN_FILE).read_text())
    for track in data["tracks"]:                      # put it back the way 1.5.0 wrote it
        for copy in track["candidates"]:
            copy.update(track.pop(COPIES_EXTRA, {}).get(copy["ref"], {}))
    (album_dir / PLAN_FILE).write_text(json.dumps(data, indent=2) + "\n")

    try:
        read_as_0_9_1(album_dir / PLAN_FILE)
    except TypeError as e:
        assert "unexpected keyword argument" in str(e)
    else:
        raise AssertionError("the old reader should not have been able to read that")


def test_repair_writes_a_whole_library_the_compatible_way(tmp_path):
    """One command, and every album that 1.1.0 to 1.5.0 wrote is readable again — the same mechanism slice
    60 used to convert a library, asking the same question: would a save write this file differently?"""
    from noaap.config import Config
    from noaap.download import rewritten
    from noaap.service import Service

    for name in ("An Album", "Another Album"):
        album_dir = tmp_path / "A Band" / name
        a_plan(album_dir)
        data = json.loads((album_dir / PLAN_FILE).read_text())
        for track in data["tracks"]:                  # as 1.5.0 wrote it
            for copy in track["candidates"]:
                copy.update(track.pop(COPIES_EXTRA, {}).get(copy["ref"], {}))
        (album_dir / PLAN_FILE).write_text(json.dumps(data, indent=2) + "\n")
        assert rewritten(album_dir, load_plan(album_dir)), "a save would write it differently"

    cfg = Config()
    cfg.library_root = tmp_path
    Service(cfg, tmp_path).repair(dry_run=False)

    for name in ("An Album", "Another Album"):
        path = tmp_path / "A Band" / name / PLAN_FILE
        read_as_0_9_1(path)  # raises if it cannot
        assert COPIES_EXTRA in json.loads(path.read_text())["tracks"][0]


def test_writing_a_plan_that_is_already_right_changes_nothing(tmp_path):
    """Idempotence, as its own case. The first version of this rewrite started from nothing, so handed
    a file it had already written it found no fields inside the candidates and **removed the record of
    them** — which `plan --verify` then reported as 116 real plans about to lose a `stream_sha`."""
    from noaap.download import narrow_candidates

    album_dir = tmp_path / "A Band" / "An Album"
    a_plan(album_dir)
    once = json.loads((album_dir / PLAN_FILE).read_text())

    twice = narrow_candidates(json.loads(json.dumps(once)))

    assert twice == once
    save_plan(load_plan(album_dir), album_dir)
    assert json.loads((album_dir / PLAN_FILE).read_text()) == once
