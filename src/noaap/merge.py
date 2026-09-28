"""Taking one library's copies into another (DESIGN §9, slice 54).

The pass proposes and a person disposes: a bare `merge` measures everything, decides nothing on
disk and prints what it would do. `--apply` is a separate act, and even then the only thing that
can remove audio is the bin.

What it reports is as much the point as what it does. Every proposal carries both files' numbers —
codec, rate, where the audio stops, length, size — because the reader has to be able to disagree
with it. A verdict nobody can check is not a verdict, it is an instruction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .download import iter_plans
from .models import AlbumPlan
from .pairing import Pair, Pairing, Side, pair, sides
from .ranking import Judgement, Verdict, consider


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


def survey(source: Path, target: Path, log: Callable[[str], None] = lambda s: None,
           judge: Callable[[Pair], Judgement] = consider) -> Survey:
    """Measure both libraries and decide, touching nothing.

    Measuring is the expensive half — two decodes a pair — so the log says how far along it is
    rather than going quiet for ten minutes over two thousand files.
    """
    incoming = list(sides(iter_plans(source)))
    existing = list(sides(iter_plans(target)))
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


def report(found: Survey, *, verdicts: Iterable[Verdict] | None = None, show_unpaired: bool = True) -> list[str]:
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
        lines.append(f"{len(found.pairing.unpaired)} track(s) are not in this library at all "
                     "— `--new` imports their albums")
    lines.append("nothing was changed. `noaap merge --apply` does it.")
    return lines


def unpaired_albums(found: Survey) -> dict[Path, list[Side]]:
    """The albums the target does not have, for `--new` to offer."""
    out: dict[Path, list[Side]] = {}
    for side in found.pairing.unpaired:
        out.setdefault(side.album_dir, []).append(side)
    return out
