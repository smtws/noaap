"""The one line a pass ends with (R-212, DESIGN §9, slice 61).

Slice 60 built the check that answers "is every track's file where the plan says" and put it in
`noaap config`. Slice 61 is what that was worth: the answer was right, and printed nowhere anybody
was looking, so one `update` copied 67 tracks of a real collection into their album roots. So the
three passes that walk a library end with it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_discs import adopt_it, discs
from test_folder_source import encode

from noaap.cli import main
from noaap.download import load_plan

LINE = "are not where their plan says"


def lose_the_files(album_dir: Path) -> None:
    """A plan that names files nobody can find — what a moved 1.5.0 library looks like."""
    plan = json.loads((album_dir / ".noaap.json").read_text())
    for track in plan["tracks"]:
        track["filename"] = "somewhere/" + Path(track["filename"]).name
    (album_dir / ".noaap.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")


def test_update_ends_with_it(discs, tmp_path, capsys):
    """`update` says it and does not fix it: rewriting a name is `repair`'s job, and the guard under
    both of them means a track whose file is missing is never fetched again anyway."""
    album_dir, _ = adopt_it(tmp_path)
    lose_the_files(album_dir)

    main(["update", "--library", str(tmp_path)])

    assert f"4 track(s) in 1 album(s) {LINE}" in capsys.readouterr().out


def test_repair_has_nothing_to_say_because_it_found_them(discs, tmp_path, capsys):
    """The other half: `repair` looks the files up where the collection says they are, so by the time
    it ends there is nothing left to report — and the plan is right again."""
    album_dir, _ = adopt_it(tmp_path)
    lose_the_files(album_dir)

    main(["repair", "--library", str(tmp_path)])

    caught = capsys.readouterr()
    # the service's own progress goes to stderr; the one line a pass ends with is output
    assert "4 track(s) were found where the collection says they are" in caught.err
    assert LINE not in caught.out and LINE not in caught.err
    assert all("/" in t.filename for t in load_plan(album_dir).tracks)


def test_repair_ends_with_it_when_a_file_really_is_gone(discs, tmp_path, capsys):
    """A track the collection cannot offer is not found, not papered over, and said out loud."""
    album_dir, plan = adopt_it(tmp_path)
    (album_dir / plan.tracks[0].filename).unlink()

    main(["repair", "--library", str(tmp_path)])

    assert f"1 track(s) in 1 album(s) {LINE}" in capsys.readouterr().out


def test_and_says_nothing_when_there_is_nothing_to_say(discs, tmp_path, capsys):
    adopt_it(tmp_path)

    main(["repair", "--library", str(tmp_path)])

    assert LINE not in capsys.readouterr().out


def test_adopt_apply_says_it_too(discs, tmp_path, capsys):
    """Where it would have been read on the day: the pass that writes the plans.

    A fresh adoption has nothing to say — the provider just told it where every file is. What it
    reports is the album **already** in the library whose files cannot be found, which is exactly the
    1.5.0 collection somebody is adopting the rest of.
    """
    album_dir, _ = adopt_it(tmp_path)
    lose_the_files(album_dir)
    for n, title in enumerate(["Alone", "Together"], 1):
        encode(tmp_path / "A Band" / "An Album" / f"{n:02d} - {title}.mp3", title=title,
               artist="A Band", album="An Album", album_artist="A Band", track=str(n))

    main(["adopt", str(tmp_path), "--library", str(tmp_path), "--apply"])

    said = capsys.readouterr().out
    assert "1 album(s) adopted" in said, "the new album was taken in"
    assert f"4 track(s) in 1 album(s) {LINE}" in said, "and the broken one was said out loud"
