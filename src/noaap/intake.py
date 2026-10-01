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

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import adopt as adopt_pass
from . import precautions, sources
from .download import load_plan, relocate, run, save_plan, would_do
from .enrich import enrich
from .models import AlbumPlan
from .treatment import Treatment

STATE = "noaap-take-in.state.json"


@dataclass
class Choices:
    """What this run does. Not recorded anywhere: see the module's last paragraph."""

    names: str = "scheme"          # "keep" leaves the collection's own filenames alone
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
        names = "their own names" if self.names == "keep" else "noaap's names"
        return f"{names}; " + (", ".join(on) if on else "nothing else")


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


def state_path(snapshot: Path) -> Path:
    return snapshot.with_name(STATE)


def read_state(path: Path) -> set[str]:
    """Which album folders are already finished, by their path relative to the root."""
    if not path.is_file():
        return set()
    try:
        return set(json.loads(path.read_text()).get("done") or [])
    except (ValueError, OSError):
        return set()


def write_state(path: Path, root: Path, done: set[str]) -> None:
    """Written after each album, so an interruption costs at most the album it was in."""
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps({"root": str(root), "at": datetime.now(UTC).isoformat(timespec="seconds"),
                               "done": sorted(done)}, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def albums_under(root: Path, source: Any) -> list[Path]:
    """Every album folder, artist by artist, in one settled order — so a resume is predictable."""
    return sorted(Path(ref.url) for ref in source.listing(str(root)))


def take_in(service: Any, root: Path, choices: Choices | None = None, *, dry_run: bool = True,
            snapshot: Path | None = None, keep: Path | None = None, resume: bool = True,
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
    folders = albums_under(root, source)
    log(f"{len(folders)} album folder(s) under {root}")
    log(f"  {choices.says()}")

    snapshot = snapshot or root.parent / precautions.SNAPSHOT
    state = state_path(snapshot)
    finished = read_state(state) if resume and not dry_run else set()
    if finished:
        log(f"  {len(finished)} album(s) were done by an earlier run and are skipped")
    if not dry_run and not snapshot.exists():
        log("writing down every file before anything is written to it …")
        precautions.take(root, snapshot, log=log)
    if keep is not None and not dry_run:
        log("  " + precautions.says_room(root, keep))

    for album_dir in folders:
        where = str(album_dir.relative_to(root))
        if where in finished:
            continue
        service.check()
        done.albums += 1
        plan = _adopted(album_dir, root, source, log=log)
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
        had_a_cover = dict(plan.cover_fetched)
        run(plan, here, service.source_for(plan), want=want, careful=True, keep=keep, keep_root=root,
            keep_as=where,          # the folder the snapshot knows, which `relocate` may have changed
            track_source=service._track_source(plan), on_track=_counted(done, service.on_track),
            check=service.check, download=False)
        if plan.cover_fetched != had_a_cover:
            done.covers += 1        # a cover was written beside this album, and the plan records it
        finished.add(where)
        write_state(state, root, finished)
        log(f"  {plan.albumartist} — {plan.album}: {len(plan.tracks)} track(s)")

    if dry_run:
        for line in done.would:
            log(f"  {line}")
        # **totals per kind** — "two albums would be taken in" is not an answer to "how many of my
        # files does this rewrite?" (the lesson of §9, slice 85, applied to somebody's own collection)
        done.renamed = sum(1 for line in done.would if "would be renamed" in line)
        done.retagged = sum(1 for line in done.would if "would be retagged" in line)
        done.covers = sum(1 for line in done.would if "a cover would be saved beside" in line)
        asked = sum(1 for line in done.would if "would ask MusicBrainz" in line)
        asked_words = sum(1 for line in done.would if "would ask LRCLIB" in line)
        log(f"{done.adopted} album(s), {done.tracks} track(s) would be taken in; "
            f"{len(done.refused)} folder(s) refused")
        log(f"{done.renamed} file(s) would be renamed, {done.retagged} audio file(s) would be "
            f"rewritten (their tags), {done.covers} album(s) would be given a cover beside them")
        if asked or asked_words:
            log(f"{asked} album(s) would be asked about at MusicBrainz, {asked_words} at LRCLIB")
        log("nothing was written. `take-in … --apply` does it.")
    else:
        log(f"{done.adopted} album(s), {done.tracks} track(s) taken in; "
            f"{len(done.refused)} folder(s) refused")
        log(f"{done.renamed} file(s) renamed, {done.retagged} audio file(s) rewritten (their tags), "
            f"{done.covers} cover(s) written beside an album, {done.lyrics} track(s) with words")
        if done.musicbrainz_requests or done.lrclib_requests:
            log(f"{done.musicbrainz_requests} MusicBrainz and {done.lrclib_requests} LRCLIB "
                "request(s) went out")
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


def _adopted(album_dir: Path, root: Path, source: Any, log: Callable[[str], None]) -> AlbumPlan | None:
    """The album's plan: the one that is there, or the one adoption would write."""
    if (plan := load_plan(album_dir)) is not None:
        return plan
    found = adopt_pass.examine(album_dir, source, root)
    if found.plan is None:
        log(f"  {album_dir.name}: {found.refused}")
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
