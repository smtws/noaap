"""Taking one library's copies into another (DESIGN §9, slice 54).

The pass proposes and a person disposes: a bare `merge` measures everything, decides nothing on
disk and prints what it would do. `--apply` is a separate act, and even then the only thing that
can remove audio is the bin.

What it reports is as much the point as what it does. Every proposal carries both files' numbers —
codec, rate, where the audio stops, length, size — because the reader has to be able to disagree
with it. A verdict nobody can check is not a verdict, it is an instruction.
"""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .download import iter_plans, save_plan
from .models import AlbumPlan, Candidate
from .pairing import Pair, Pairing, Side, pair, sides
from .plan import wanted_filename
from .ranking import Facts, Judgement, Verdict, consider
from .recycle import bin_track
from .service import _inside
from .text import key as text_key


@dataclass
class Proposal:
    pair: Pair
    verdict: Judgement

    @property
    def album(self) -> AlbumPlan:
        return self.pair.old.plan

    @property
    def album_dir(self) -> Path:
        return self.pair.old.album_dir


@dataclass
class Survey:
    """Everything the pass concluded, grouped the way a person reads it: by album."""

    proposals: list[Proposal] = field(default_factory=list)
    pairing: Pairing = field(default_factory=Pairing)

    def by_verdict(self, *wanted: Verdict) -> list[Proposal]:
        return [p for p in self.proposals if p.verdict.verdict in wanted]

    @property
    def acting(self) -> list[Proposal]:
        return [p for p in self.proposals if p.verdict.acts]

    def counts(self) -> dict[str, int]:
        out = {v.value: 0 for v in Verdict}
        for p in self.proposals:
            out[p.verdict.verdict.value] += 1
        return out

    def bytes_moved(self) -> tuple[int, int]:
        """(added to the library, moved to the bin) — for the acting proposals only."""
        added = sum(p.verdict.new.bytes or 0 for p in self.acting)
        binned = sum((p.verdict.old.bytes or 0) if p.verdict.old else 0 for p in self.acting)
        return added, binned

    def albums(self) -> dict[Path, list[Proposal]]:
        out: dict[Path, list[Proposal]] = {}
        for p in self.proposals:
            out.setdefault(p.album_dir, []).append(p)
        return out


def wanted(side: Side, artist: str | None, album: str | None) -> bool:
    """Is this track inside the scope the user named? An unnamed scope is every track."""
    if artist and text_key(side.plan.albumartist) != text_key(artist):
        return False
    return not (album and text_key(side.plan.album) != text_key(album))


def in_scope(incoming: list[Side], existing: list[Side], artist: str | None,
             album: str | None) -> tuple[list[Side], list[Side]]:
    """`--only` narrows both sides; `--album` narrows only the library being changed.

    An album's name is rarely the same on both sides — a folder called "Trust In Rust" answers to a
    library album called "Trust in Rust (Deluxe Edition)" — so filtering the incoming side by album
    would drop exactly the tracks the user asked to merge into it. The artist is safe on both.
    """
    return ([s for s in incoming if wanted(s, artist, None)],
            [s for s in existing if wanted(s, artist, album)])


def survey(source: Path, target: Path, log: Callable[[str], None] = lambda s: None,
           judge: Callable[[Pair], Judgement] = consider,
           artist: str | None = None, album: str | None = None) -> Survey:
    """Measure both libraries and decide, touching nothing.

    Measuring is the expensive half — two decodes a pair — so the log says how far along it is
    rather than going quiet for ten minutes over two thousand files.
    """
    incoming, existing = in_scope(list(sides(iter_plans(source))), list(sides(iter_plans(target))),
                                  artist, album)
    if artist or album:
        log(f"only {album or artist}")
    log(f"{len(incoming)} tracks here, {len(existing)} there")
    found = pair(incoming, existing)
    log(f"{found.counts()['pairs']} pairs, {found.counts()['ambiguous']} ambiguous, "
        f"{found.counts()['unpaired']} the library does not have")

    out = Survey(pairing=found)
    for n, p in enumerate(found.pairs, 1):
        out.proposals.append(Proposal(p, judge(p)))
        if n % 50 == 0:
            log(f"  measured {n}/{len(found.pairs)}")
    return out


# -- what a person reads ---------------------------------------------------------------------------


def describe(proposal: Proposal) -> list[str]:
    """One proposal, with the numbers that produced it."""
    j = proposal.verdict
    head = f"  {j.verdict.value:9} {proposal.pair.new.track.artist} - {proposal.pair.new.track.title}"
    lines = [head, f"      {j.why}"]
    lines.append(f"      here:     {j.old.line() if j.old else 'nothing'}")
    lines.append(f"      incoming: {j.new.line()}")
    return lines


def report(found: Survey, *, verdicts: Iterable[Verdict] | None = None, show_unpaired: bool = True,
           applying: bool = False) -> list[str]:
    """The whole pass, album by album, in the order a person would look at it."""
    wanted = set(verdicts) if verdicts else {Verdict.REPLACE, Verdict.FILL, Verdict.UNDECIDED}
    lines: list[str] = []
    for album_dir, proposals in sorted(found.albums().items()):
        shown = [p for p in proposals if p.verdict.verdict in wanted]
        if not shown:
            continue
        plan = proposals[0].album
        lines.append(f"{plan.albumartist} — {plan.album}  ({album_dir.name})")
        for p in shown:
            lines += describe(p)
        lines.append("")

    counts = found.counts()
    added, binned = found.bytes_moved()
    lines.append(f"{counts['replace']} to replace, {counts['fill']} to fill, "
                 f"{counts['keep']} to keep, {counts['undecided']} undecided")
    if found.acting:
        lines.append(f"{added / 1e9:.2f} GB would be added, {binned / 1e9:.2f} GB moved to the bin")
    else:
        lines.append("nothing would be added and nothing binned")
    if show_unpaired and found.pairing.ambiguous:
        lines.append(f"{len(found.pairing.ambiguous)} track(s) match more than one here and are left alone")
    if show_unpaired and found.pairing.unpaired:
        albums = len(unpaired_albums(found))
        lines.append(f"{len(found.pairing.unpaired)} track(s) in {albums} album(s) are not in this "
                     "library at all — `--new` fetches those albums")
    lines.append("about to do it." if applying else "nothing was changed. `noaap merge --apply` does it.")
    return lines


# -- and what `--apply` does -------------------------------------------------------------------------


def numbers(proposal: Proposal) -> dict[str, object]:
    """Both files' measurements, for the bin entry.

    `chosen.ref` is what a restore reads to refuse the displacer: the user has just said they
    preferred what was here, so this ref is never offered for that track again (§9, slice 49).
    """
    j = proposal.verdict
    out: dict[str, object] = {
        "chosen": {"ref": proposal.pair.new.track.video_id, "how": j.why, **_facts(j.new)},
    }
    if j.old:
        out["displaced"] = _facts(j.old)
    return out


def remember(candidate: Candidate | None, facts: Facts) -> None:
    """Write a measurement onto the candidate it is about. An absent number is left absent."""
    if candidate is None:
        return
    for name, value in (("length", facts.length), ("codec", facts.codec), ("bitrate", facts.bitrate),
                        ("bytes", facts.bytes), ("cutoff_khz", facts.band.cutoff)):
        if value is not None:
            setattr(candidate, name, value)
    if facts.band.known:
        candidate.full_band = facts.band.full


def _facts(f: Facts) -> dict[str, object]:
    return {"codec": f.codec, "bitrate": f.bitrate, "bytes": f.bytes, "length": f.length,
            "cutoff_khz": f.band.cutoff, "full_band": f.band.full}


def carry_out(found: Survey, library: Path, log: Callable[[str], None] = lambda s: None) -> dict[str, int]:
    """Do what the survey proposed. The only thing that removes audio here is the bin.

    Each replacement is one album's plan saved at a time, so an interruption leaves a library that
    is consistent up to the track it was working on rather than a half-written pass.
    """
    done = {"replaced": 0, "filled": 0, "failed": 0}
    for proposal in found.acting:
        try:
            _take(proposal, library, log)
        except OSError as e:  # one file's trouble is not the pass's
            done["failed"] += 1
            log(f"  could not take {proposal.pair.new.track.title}: {e}")
            continue
        done["filled" if proposal.verdict.verdict is Verdict.FILL else "replaced"] += 1
    return done


def _take(proposal: Proposal, library: Path, log: Callable[[str], None]) -> None:
    """One track: copy the incoming file in, bin what was there, and record both."""
    old, new = proposal.pair.old, proposal.pair.new
    plan, track, album_dir = old.plan, old.track, old.album_dir
    incoming = new.file
    if incoming is None or not incoming.is_file():
        raise OSError(f"the file is no longer at {new.track.video_id}")

    # `_inside` takes a *name* and answers with the path only if it really is one file directly
    # in this album folder — a plan is a file on disk and a tampered one can name `../../something`
    was = _inside(album_dir, track.filename) if track.filename else None
    # the file decides its own extension, as it has since slice 53
    track.ext = incoming.suffix.lstrip(".").lower() or track.ext
    wanted = wanted_filename(plan, track)

    # Ruling 5's other half. `write_sidecar` records what timed words were written against, but a
    # sidecar older than that field says nothing — and most of this library's do. At the moment of
    # a replacement we know exactly what the stamps belong to: the file about to go to the bin,
    # whose length was just measured. Recording it here is what lets `timings_stale` speak
    # afterwards (§9, slice 54).
    if track.lyrics == "synced" and not track.lyrics_for_source and proposal.verdict.old:
        track.lyrics_for_source = old.track.effective_id
        track.lyrics_for_length = proposal.verdict.old.length

    if was and was.is_file():
        bin_track(library, album_dir, plan, track,
                  reason=f"replaced by a copy from {new.plan.provider}: {proposal.verdict.why}",
                  audio=was, ranking=numbers(proposal))

    album_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(incoming, album_dir / wanted)
    track.filename = wanted
    track.file_length = proposal.verdict.new.length
    track.tagged = None  # the ordinary pass retags it, with this album's names

    ref = new.track.video_id
    if not track.candidate(ref):
        track.candidates.append(Candidate(ref=ref, provider=new.plan.provider, added_by="pass",
                                          why=proposal.verdict.why, when=dt.date.today().isoformat()))
    # both sides keep what was measured, so the next pass decides nothing twice and the panel has
    # the same numbers the report printed (§9, slice 54)
    remember(track.candidate(ref), proposal.verdict.new)
    if track.candidate(old_ref := old.track.effective_id) and proposal.verdict.old:
        remember(track.candidate(old_ref), proposal.verdict.old)
    track.chosen = track.source_override = ref
    save_plan(plan, album_dir)
    log(f"  took {track.artist} - {track.title} ({proposal.verdict.why})")


def unpaired_albums(found: Survey) -> dict[Path, list[Side]]:
    """The albums the target does not have, for `--new` to offer."""
    out: dict[Path, list[Side]] = {}
    for side in found.pairing.unpaired:
        out.setdefault(side.album_dir, []).append(side)
    return out


def take_new(found: Survey, fetch: Callable[[str], object],
             log: Callable[[str], None] = lambda s: None) -> dict[str, int]:
    """Fetch the albums this library does not have, by the ordinary path.

    Nothing special happens here: each one goes through `fetch`, which already refuses to touch an
    album whose artist and name are in the library and says how many titles overlap instead
    (§9, slice 53). **A deluxe edition never quietly grows the album that is already here.**
    """
    done = {"taken": 0, "held": 0}
    for album_dir, tracks in sorted(unpaired_albums(found).items()):
        plan = tracks[0].plan
        log(f"  {plan.albumartist} — {plan.album} ({len(tracks)} track(s) this library lacks)")
        outcome = fetch(plan.source_url)
        status = getattr(outcome, "status", "")
        done["held" if status == "held" else "taken"] += 1
    return done
