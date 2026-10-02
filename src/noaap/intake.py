"""Taking a whole collection in, album by album, so that all of it ends in one state (§9, slice 101).

The user's is twenty years of music on a NAS: about eleven thousand tracks, no plans, no sidecars,
and no backup. `noaap take-in <root>` walks it once and leaves every album in the state the library
is in — adopted, looked up, covered, worded, tagged — instead of the four passes it used to take.

**Everything about it is arranged around being interrupted.** Before the first write there is a
snapshot (§9, slice 99); every audio file is written the careful way; and a state file beside the
snapshot records each album as it is finished, so a pass that stops — a laptop closed, a NAS that
goes away, a person who changes their mind — is resumed by running the same command again and does
not repeat a single album.

**The switches mean "do this now"**, and nothing about them is recorded on the album: the settings
are what the library's state is (§9, slice 100), and a later `repair` brings every album to them.
Taking eleven thousand tracks in without lookups to get them onto the page quickly is therefore a
decision about *tonight*, not about the collection.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import adopt as adopt_pass
from . import config as config_mod
from . import precautions, sources, sources_folder
from .download import load_plan, relocate, run, save_plan, would_do
from .enrich import enrich
from .models import AlbumPlan
from .plan import safe_name
from .text import key as text_key
from .treatment import Treatment

STATE = "take-in.json"      # the suffix; the name is the snapshot's own (see `state_path`)
MADE = "made.json"          # and what the pass created, so a restore can undo that too


@dataclass
class Choices:
    """What this run does. Not recorded anywhere: see the module's last paragraph."""

    names: str = "scheme"          # "keep" leaves the collection's own filenames alone
    names_from: str = ""           # and where that came from, for the line the run opens with
    musicbrainz: bool = True
    lyrics: bool = True
    cover_beside: bool = True
    cover_embedded: bool = True
    lyrics_embedded: bool = True
    tags: bool = True

    def as_treatment(self) -> Treatment:
        """The same choices as a treatment, for this run only."""
        return Treatment(musicbrainz=self.musicbrainz, lyrics=self.lyrics,
                         cover_beside=self.cover_beside, cover_embedded=self.cover_embedded,
                         lyrics_embedded=self.lyrics_embedded,
                         rename_adopted=self.names == "scheme", retag_adopted=self.tags)

    def says(self) -> str:
        on = [key for key in ("musicbrainz", "lyrics", "cover_beside", "cover_embedded",
                              "lyrics_embedded", "tags") if getattr(self, key)]
        return f"{self.says_names()}; " + (", ".join(on) if on else "nothing else")

    def says_names(self) -> str:
        """Which names this run gives the files, **and where that was decided** (R-410, ruling 3).

        The flag used to default to `scheme` while the setting it mirrors, `rename_adopted`,
        defaults to off and the page says so. A run without the flag therefore renamed 2,847 files
        of a collection whose owner had been told renaming was off.
        """
        names = "their own names" if self.names == "keep" else "noaap's names"
        return f"{names}{f' (from {self.names_from})' if self.names_from else ''}"


@dataclass
class Progress:
    """What the pass has done, and what it cost — the numbers a report is made of."""

    albums: int = 0
    tracks: int = 0
    adopted: int = 0
    refused: list[str] = field(default_factory=list)
    renamed: int = 0
    retagged: int = 0
    covers: int = 0
    lyrics: int = 0
    musicbrainz_requests: int = 0
    lrclib_requests: int = 0
    would: list[str] = field(default_factory=list)
    stopped: str = ""           # the one line a pass that could not start printed (R-417)


def state_path(snapshot: Path) -> Path:
    """Named after the snapshot it belongs to, because two collections can share a directory.

    It was one fixed name beside the snapshot, and two snapshots in one folder then shared a single
    resume file. Found while measuring: a second copy of the same collection, with its own snapshot
    in the same directory, read the first copy's state, decided all 133 albums were done and took in
    nothing in a tenth of a second — reporting `0 albums` as if that were the answer.
    """
    return snapshot.with_name(f"{snapshot.stem}.{STATE}")


def made_path(snapshot: Path) -> Path:
    """Where the pass writes down what it *created*, so a restore can take it away again.

    The snapshot says what was there; this says what was not. A restore that only put files back
    left noaap's own leavings behind — one plan per album, the covers it fetched, the words it
    saved — so the folder came back with 136 files in it that its owner never had (R-342, ruling 3:
    "a way back" means the folder as it was).
    """
    return snapshot.with_name(f"{snapshot.stem}.{MADE}")


def read_made(path: Path, root: Path | None = None, folders: bool = False) -> list[str]:
    """What a pass recorded as its own, for this root. Another root's record is not this one's.

    With `folders`, the directories it made instead of the files: the album folder a rename moved an
    album into. A restore puts the files back under their recorded paths, which leaves those standing
    **empty** — 19 of them on the user's own collection, husks of noaap's spelling of their albums.
    """
    if not path.is_file():
        return []
    try:
        got = json.loads(path.read_text())
    except (ValueError, OSError):
        return []
    if root is not None and got.get("root") not in (None, str(root), str(root.resolve())):
        return []
    return [str(name) for name in (got.get("folders" if folders else "made") or [])]


def write_made(path: Path, root: Path, made: list[str], folders: list[str] = ()) -> None:
    """Written after each album, like the resume file: an interruption loses nothing but its album."""
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps({"root": str(root), "at": datetime.now(UTC).isoformat(timespec="seconds"),
                               "made": sorted(dict.fromkeys(made)),
                               "folders": sorted(dict.fromkeys(folders))},
                              ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def beside(folder: Path) -> set[str]:
    """What is in an album folder that is not audio — a plan, a cover, an `.lrc`, their own scan.

    Asked before a pass touches the album and again afterwards; the difference is what the pass
    created. Measured rather than predicted, so a writer that gains a file does not have to remember
    to say so.
    """
    return {str(p.relative_to(folder)) for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() not in precautions.AUDIO
            and not p.name.endswith(".part") and ".noaap-new" not in p.name}


def read_state(path: Path, root: Path | None = None) -> set[str]:
    """Which album folders are already finished, by their path relative to the root.

    **A state file belonging to another root is not this run's.** The root was written into the file
    from the first version and never read back; checking it is what makes a stale or shared file
    harmless rather than silent.
    """
    if not path.is_file():
        return set()
    try:
        got = json.loads(path.read_text())
    except (ValueError, OSError):
        return set()
    if root is not None and got.get("root") not in (None, str(root), str(root.resolve())):
        return set()
    return set(got.get("done") or [])


def write_state(path: Path, root: Path, done: set[str]) -> None:
    """Written after each album, so an interruption costs at most the album it was in."""
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps({"root": str(root), "at": datetime.now(UTC).isoformat(timespec="seconds"),
                               "done": sorted(done)}, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def album_refs(root: Path, source: Any) -> list[Any]:
    """Every album folder as the source saw it, in one settled order — the names included.

    The listing already reads the first file of every folder, so what an album calls itself is known
    before anything is adopted. That is what makes the collision check below cost nothing.
    """
    return sorted(source.listing(str(root)), key=lambda ref: ref.url)


def albums_under(root: Path, source: Any) -> list[Path]:
    """Every album folder, artist by artist, in one settled order — so a resume is predictable."""
    return [Path(ref.url) for ref in album_refs(root, source)]


def under_only(albums: Iterable[Path], root: Path, only: Iterable[str]) -> tuple[list[Path], list[str]]:
    """The albums `--only` chooses, and the values that chose nothing (R-417, point 1).

    A value is a path relative to the root — `Crematory`, `Crematory/Act Seven` — and an album is
    chosen when that path is the album or holds it. Matched component by component through
    `text_key`, as `adopt --only` and `merge --only` match an artist, so the same spelling works
    everywhere. **The root and the library stay the collection**, which is the point: every name the
    scheme gives is the one the whole pass would give.
    """
    wanted = [tuple(text_key(part) for part in Path(value.strip("/")).parts) for value in only]
    chosen: list[Path] = []
    hit = [False] * len(wanted)
    for album in albums:
        parts = tuple(text_key(part) for part in album.relative_to(root).parts)
        for n, want in enumerate(wanted):
            if parts[:len(want)] == want:
                chosen.append(album)
                hit[n] = True
                break
    return chosen, [value for value, found in zip(only, hit, strict=True) if not found]


def says_only(only: Iterable[str], chosen: Iterable[Path]) -> str:
    """`only: Crematory (21 albums)` — the first thing a run of a part of a collection says."""
    n = len(list(chosen))
    return "only: " + ", ".join(str(value) for value in only) + f" ({n} album{'s' if n != 1 else ''})"


def folder_clashes(refs: Iterable[Any], root: Path) -> dict[str, list[Path]]:
    """`{the one folder: the albums that would all become it}` (R-410, ruling 1).

    Three sibling folders of a box set — `… - Gestern`, `… - Heute`, `… - Morgen` — state one album
    between them, so the scheme gives all three the same name. Whichever moved first would own the
    folder and the others would stay where they are, half the box filed and half not, with three
    plans aimed at one directory. None of them moves: the set is named and left as it is.
    """
    want: dict[str, list[Path]] = {}
    for ref in refs:
        folder = Path(ref.url)
        artist = (ref.artist or folder.parent.name).strip()
        want.setdefault(f"{safe_name(artist)}/{safe_name((ref.title or folder.name).strip())}",
                        []).append(folder)
    return {target: folders for target, folders in want.items()
            if len(folders) > 1 and any(str(f.relative_to(root)) != target for f in folders)}


def not_taken_in(root: Path, albums: Iterable[Path], refused: dict[str, str] | None = None,
                 under: list[Path] | None = None) -> list[tuple[str, str, int]]:
    """Every folder holding audio that no album of this pass covers, with why and how many (R-410).

    A collection of twenty years has corners: a folder named with an ellipsis, a disc folder spelled
    `1-3`, a box with an empty first disc, an artist folder inside an artist folder, thirteen mp3s in
    a `.thumb`. Each of those was passed over without a word — 127 files of the user's own — and the
    only way to learn of it was to count the plan against the tree by hand. **Nothing with audio in
    it is silent**: what is not taken in is named, with the reason, in the dry run and the apply.
    """
    covered = set(albums)
    out: list[tuple[str, str, int]] = []
    walked: set[Path] = set()
    for start in (under if under is not None else [root]):
        for here, dirs, names in os.walk(start):
            folder = Path(here)
            if folder in walked:
                continue
            walked.add(folder)
            dirs.sort()
            files = [n for n in names if Path(n).suffix.lower() in sources_folder.AUDIO
                     and not sources_folder.is_hidden_name(n)]
            if not files:
                continue
            rel = folder.relative_to(root)
            parts = rel.parts
            # an album folder the pass refused is named here too: it was looked at, and not taken in
            if folder in covered and str(rel) not in (refused or {}):
                continue
            if folder.parent in covered and sources_folder.DISC_FOLDER.fullmatch(folder.name.strip()):
                continue                                   # a disc of an album that is taken in
            where = str(rel) if parts else "."
            if any(sources_folder.is_hidden_name(part) for part in parts):
                why = "hidden, left alone"
            elif said := (refused or {}).get(where):
                why = said
                if inside := [d for d in dirs if not sources_folder.is_hidden_name(d)]:
                    why += f"; it also holds {len(inside)} folder(s) of its own"
            elif folder.parent in covered:
                why = f"inside {folder.parent.relative_to(root)}, which is read as one album"
            elif len(parts) > 2:
                why = "one level too deep — an album is <artist>/<album> under the collection"
            elif not parts:
                why = "loose in the collection, outside any artist folder"
            else:
                why = "not read as an album"
            out.append((where, why, len(files)))
    return sorted(out)


def says_not_taken_in(rows: list[tuple[str, str, int]]) -> list[str]:
    """The section a pass ends with. Empty when every file is accounted for."""
    if not rows:
        return []
    files = sum(n for _, _, n in rows)
    return [f"not taken in — {len(rows)} folder(s), {files} audio file(s):"] + [
        f"  {where} — {why}, {n} file(s)" for where, why, n in rows]


def says_clashes(clashes: dict[str, list[Path]], root: Path) -> list[str]:
    """One line per set, naming every folder in it.

    **It does not say they are the same album** (R-418): the user's Hans Söllner pair is one album
    split over two folders, track 11 alone in the second, and "would all become X" read as if noaap
    had found duplicates. What is true is narrower — the scheme would file them under one name, and
    noaap does not merge folders.
    """
    return [f"⚠ {' and '.join(str(f.relative_to(root)) for f in folders)} would be filed under one "
            f"name, {target}; noaap does not merge folders — left as they are"
            for target, folders in sorted(clashes.items())]


def names_against(root: Path, library: Any) -> str:
    """Why a pass that renames cannot be pointed below its library (R-417, point 2).

    The scheme gives an album `<album artist>/<album>` **under the base it is filed against**, and
    the two passes do not pick the same base: a staged run hands its inner pass the copy it made, so
    the base is the take-in root, while the dry run asks the configured library. Measured on a copy:
    pointed at `…/Musik/Crematory` with the library at `…/Musik`, the staged dry run said no folder
    would move and the apply made `…/Musik/Crematory/Crematory/Act Seven`; the plain pass has the
    same flaw the other way, promising a move in the dry run and moving nothing. Neither base is
    right for an artist folder — the scheme would file the artist inside itself either way — so the
    pass is refused rather than guessing, and `--only` is how one part of a collection is taken in.
    """
    where = Path(str(library)).expanduser()
    if root.resolve() == where.resolve():
        return ""
    return (f"this run renames into noaap's scheme, and the folder it is pointed at is not the "
            f"library it would name things against: root {root}, library {where}. Point it at "
            f"{where} — `--only {root.name}` takes in that part of it — or add `--names keep`.")


def take_in(service: Any, root: Path, choices: Choices | None = None, *, dry_run: bool = True,
            snapshot: Path | None = None, keep: Path | None = None, resume: bool = True,
            say_leftovers: bool = True, only: Iterable[str] = (),
            log: Callable[[str], None] = lambda s: None) -> Progress:
    """Adopt every album under `root`, look it up, and bring it to the chosen state.

    Dry by default, like every other pass here: `dry_run=True` reads the whole tree, says what each
    album would get — every rename, every tag that would change, every lookup it would make — and
    writes nothing at all, not even the snapshot.
    """
    choices = choices or Choices()
    want = choices.as_treatment()
    cfg = service.cfg
    source = sources.get("folder", cfg)
    # **an adoption does not rank copies, so it does not digest every file** (§9, slice 101). Measured
    # on the user's own collection: with the digests, reading 2000 files to say what would happen cost
    # 43.8 GB from the device and 172 s — the whole collection, for a dry run.
    source.digests = False
    done = Progress()
    # **a pass that renames is refused below its library** (R-417, point 2), before it plans
    if choices.names == "scheme" and (why := names_against(root, service.library)):
        done.stopped = why
        log(why)
        return done
    refs = album_refs(root, source)
    folders = [Path(ref.url) for ref in refs]
    chose: list[Path] | None = None
    if only := list(only):
        # **a part of the collection, with the whole collection's names** (R-417, point 1)
        folders, empty = under_only(folders, root, only)
        if empty:
            # a wrong argument, which this program answers with one sentence and exit 2 (R-421)
            raise config_mod.Refused(f"--only {', '.join(empty)}: no album under that path")
        chosen = set(folders)
        refs = [ref for ref in refs if Path(ref.url) in chosen]
        chose = [root / Path(value.strip("/")) for value in only]
        log(says_only(only, folders))
    log(f"{len(folders)} album folder(s) under {root}")
    log(f"  {choices.says()}")
    # **no two albums are filed under one name** (R-410, ruling 1). Asked before the first move, off
    # the listing that has already been read, so the whole set is named while every one of them is
    # still where its owner put it.
    clashing: set[Path] = set()
    if want.rename_adopted:
        found = folder_clashes(refs, root)
        for line in says_clashes(found, root):
            log(line)
            if dry_run:
                done.would.append(line)
        clashing = {folder for folders_ in found.values() for folder in folders_}
    # why each folder was passed over, for the section this pass ends with (R-410, ruling 2)
    why_refused: dict[str, str] = {str(folder.relative_to(root)): "would be filed under a name "
                                   "another album of yours would get too"
                                   for folder in clashing}

    snapshot = snapshot or precautions.snapshot_for(root)
    state, made_at = state_path(snapshot), made_path(snapshot)
    finished = read_state(state, root) if resume and not dry_run else set()
    made = read_made(made_at, root) if resume and not dry_run else []
    made_folders = read_made(made_at, root, folders=True) if resume and not dry_run else []
    if finished:
        log(f"  {len(finished)} album(s) were done by an earlier run and are skipped")
    if not dry_run and not snapshot.exists():
        log("writing down every file before anything is written to it …")
        precautions.take(root, snapshot, log=log)
    if keep is not None and not dry_run:
        log("  " + precautions.says_room(root, keep))
    # **what the snapshot already measured, so a careful write reads one file instead of two**
    # (R-342, ruling 4). A couple of megabytes of strings for eleven thousand tracks, read once.
    recorded: dict[str, str] = {}
    if not dry_run and snapshot.is_file():
        recorded = {r.path: r.audio for r in precautions.read(snapshot).files
                    if r.audio and r.is_audio}
        log(f"  {len(recorded)} file(s) already have a digest from the snapshot")

    for album_dir in folders:
        where = str(album_dir.relative_to(root))
        if where in finished:
            continue
        service.check()
        done.albums += 1
        if album_dir in clashing:
            done.refused.append(where)
            continue
        # what is in the folder besides its audio, before this pass has written anything into it
        was_beside = beside(album_dir)
        plan = _adopted(album_dir, root, source, log=log, refused=why_refused)
        if plan is None:
            done.refused.append(where)
            continue
        done.adopted += 1
        done.tracks += len(plan.tracks)
        if dry_run:
            done.would.extend(_would(service, plan, album_dir, root, choices, want))
            continue
        save_plan(plan, album_dir)
        _look_up(service, plan, album_dir, choices, done, log=log)
        # **this run's choices, not the settings.** `Service._run` asks what the library's state is,
        # which is right for update and repair and wrong here: a take-in is told what to do tonight
        # (§9, slice 100), and the album is brought to the settings by the next repair like any other.
        here = album_dir
        if root.resolve() == service.library.resolve():
            here = relocate(album_dir, plan, service.library, want)   # only where the root *is* the library
        if here != album_dir and here.is_dir():
            # the folder this pass moved the album into, so a restore does not leave it standing empty
            made_folders.append(str(here.relative_to(root)))
        had_a_cover = dict(plan.cover_fetched)
        run(plan, here, service.source_for(plan), want=want, careful=True, keep=keep, keep_root=root,
            keep_as=where,          # the folder the snapshot knows, which `relocate` may have changed
            expect=lambda name, folder=where: recorded.get(f"{folder}/{name}"),
            track_source=service._track_source(plan), on_track=_counted(done, service.on_track),
            check=service.check, download=False)
        if plan.cover_fetched != had_a_cover:
            done.covers += 1        # a cover was written beside this album, and the plan records it
        # **what this pass put in the folder, so a restore can take it away** (§9, slice 102). The
        # difference between before and after, not a list some writer has to remember to add to.
        made += [str((here / name).relative_to(root)) for name in beside(here) - was_beside]
        finished.add(where)
        write_state(state, root, finished)
        write_made(made_at, root, made, made_folders)
        log(f"  {plan.albumartist} — {plan.album}: {len(plan.tracks)} track(s)")

    if dry_run:
        for line in done.would:
            log(f"  {line}")
        # **totals per kind** — "two albums would be taken in" is not an answer to "how many of my
        # files does this rewrite?" (the lesson of §9, slice 85, applied to somebody's own collection)
        done.renamed = sum(1 for line in done.would if "would be renamed" in line)
        # **both lines mean the file is rewritten**: one says which values change, the other that
        # none do and the plan's record of them is out of date. Counting only the first said 1896
        # where the pass then rewrote 2000 — a dry run that undercounts by 104 files is the fault
        # §9 slice 85 exists for, in the arithmetic this time rather than in the lines.
        done.retagged = sum(1 for line in done.would
                            if "would be retagged" in line or "would be rewritten" in line)
        done.covers = sum(1 for line in done.would if "a cover would be saved beside" in line)
        asked = sum(1 for line in done.would if "would ask MusicBrainz" in line)
        asked_words = sum(1 for line in done.would if "would ask LRCLIB" in line)
        log(f"{done.adopted} album(s), {done.tracks} track(s) would be taken in; "
            f"{len(done.refused)} folder(s) refused")
        log(f"{done.renamed} file(s) would be renamed, {done.retagged} audio file(s) would be "
            f"rewritten (their tags), {done.covers} album(s) would be asked for a cover")
        if asked or asked_words:
            log(f"{asked} album(s) would be asked about at MusicBrainz, {asked_words} at LRCLIB")
        for folder in precautions.empty_under(root, everything=bool(cfg.remove_empty_folders)):
            log(f"  would remove the empty folder {folder.relative_to(root)}")
        if say_leftovers:
            for line in says_not_taken_in(not_taken_in(root, folders, why_refused, chose)):
                log(line)
                done.would.append(line)
        log("nothing was written. `take-in … --apply` does it.")
    else:
        log(f"{done.adopted} album(s), {done.tracks} track(s) taken in; "
            f"{len(done.refused)} folder(s) refused")
        log(f"{done.renamed} file(s) renamed, {done.retagged} audio file(s) rewritten (their tags), "
            f"{done.covers} cover(s) written beside an album, {done.lyrics} track(s) with words")
        for folder in precautions.empty_under(root, everything=bool(cfg.remove_empty_folders)):
            with contextlib.suppress(OSError):
                folder.rmdir()
            log(f"  removed the empty folder {folder.relative_to(root)}")
        if done.musicbrainz_requests or done.lrclib_requests:
            log(f"{done.musicbrainz_requests} MusicBrainz and {done.lrclib_requests} LRCLIB "
                "request(s) went out")
        # **the apply says it too** (R-410, ruling 2): the folders that are still their owner's,
        # read after the pass, so a folder it emptied or renamed is not reported as left behind.
        if say_leftovers:
            for line in says_not_taken_in(
                    not_taken_in(root, albums_under(root, source), why_refused, chose)):
                log(line)
    return done


def _counted(done: Progress, inner: Callable[[Any, str], None]) -> Callable[[Any, str], None]:
    """Count what the pass really did, in the words the pass itself uses for it.

    The dry run counted its own lines and the real run counted nothing, so a measured pass over 2000
    files reported `renamed: 0, retagged: 0` — which is how the first real run's numbers said nothing
    about the 1662 files it had failed to tag.
    """
    def said(track: Any, what: str) -> None:
        if what == "renamed":
            done.renamed += 1
        elif what == "retagged" or what.startswith("lyrics ("):
            done.retagged += 1
        inner(track, what)
    return said


def _adopted(album_dir: Path, root: Path, source: Any, log: Callable[[str], None],
             refused: dict[str, str] | None = None) -> AlbumPlan | None:
    """The album's plan: the one that is there, or the one adoption would write."""
    if (plan := load_plan(album_dir)) is not None:
        return plan
    found = adopt_pass.examine(album_dir, source, root)
    if found.plan is None:
        log(f"  {album_dir.name}: {found.refused}")
        # written down as well as said, so the pass's "not taken in" section can name it (R-410)
        if refused is not None:
            refused[str(album_dir.relative_to(root))] = found.refused or "refused"
        return None
    return found.plan


def _look_up(service: Any, plan: AlbumPlan, album_dir: Path, choices: Choices, done: Progress,
             log: Callable[[str], None]) -> None:
    """MusicBrainz and LRCLIB, each only if this run asks for it and the album may be looked up."""
    if choices.musicbrainz and service.may_look_up(plan) and (mb := service.mb):
        before = getattr(mb, "requests", 0)
        stats = enrich(plan, mb, progress=lambda m: service.check(), source=service.source_for(plan))
        done.musicbrainz_requests += max(0, getattr(mb, "requests", 0) - before)
        if stats.get("release") or stats.get("tracks"):
            log(f"    MusicBrainz: {'release matched' if stats.get('release') else str(stats.get('tracks')) + ' track(s)'}")
        save_plan(plan, album_dir)
    if choices.lyrics and service.may_look_up(plan) and (api := service.lrclib):
        before = getattr(api, "requests", 0)
        service._lyrics_pass(plan, album_dir, api)
        done.lrclib_requests += max(0, getattr(api, "requests", 0) - before)
        done.lyrics += sum(1 for t in plan.tracks if t.lyrics)


def _would(service: Any, plan: AlbumPlan, album_dir: Path, root: Path, choices: Choices,
           want: Treatment) -> list[str]:
    """What this album would get, in the lines the passes themselves print."""
    said = [f"{plan.albumartist} — {plan.album} ({len(plan.tracks)} track(s), {album_dir.relative_to(root)})"]
    if choices.musicbrainz and service.may_look_up(plan):
        said.append(f"  would ask MusicBrainz about {len(plan.tracks)} track(s)")
    if choices.lyrics and service.may_look_up(plan):
        said.append(f"  would ask LRCLIB about {sum(1 for t in plan.tracks if t.lyrics is None)} track(s)")
    cover = service._cover_of(plan, album_dir)
    said += [f"  {line}" for line in would_do(plan, album_dir, cover, service.library, want)]
    return said


def as_dict(progress: Progress) -> dict[str, Any]:
    return asdict(progress)


__all__ = [
    "STATE",
    "Choices",
    "Progress",
    "albums_under",
    "read_state",
    "state_path",
    "take_in",
    "write_state",
]
