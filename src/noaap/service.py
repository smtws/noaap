"""Orchestration shared by the CLI and the web UI (DESIGN.md §7: the library never knows the UI).

Everything user-visible goes through three callbacks: `log(text)`, `on_plan(plan)` before
anything is downloaded, and `on_track(track, what)` per track.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import tempfile
import threading
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import sources
from .config import Config
from .download import (
    MAYBE_COVER,
    PARTS_DIR,
    PLAN_FILE,
    find_plan,
    iter_plans,
    load_plan,
    lost_files,
    needs_a_recut,
    point_at,
    relocate,
    rewritten,
    run,
    save_plan,
    says_the_old_name,
    would_do,
)
from .enrich import enrich
from .lyrics import (
    Lrclib,
    LyricsAPI,
    LyricsError,
    fit_reason,
    fit_verdict,
    nominated,
    plain_text,
    publishable,
    read_sidecar,
    reconcile,
    remove_sidecar,
    sent_sha,
    sidecar_lost,
    sidecar_path,
    status_of,
    update_track,
    user_owns,
    write_sidecar,
)
from .lyrics import default_cache_path as lyrics_cache_path
from .mb import MusicBrainz, default_cache_path
from .models import AlbumPlan, Candidate, Failure, Kind, PlanTrack, Provenance, SourceRef
from .plan import (
    build_plan,
    drop_album_name,
    merge_plans,
    refresh_derived,
    set_single_album_name,
    states_numbers,
    wanted_filename,
    wanted_folder,
)
from .precautions import empty_under
from .recycle import DELETED, PRUNED, Entry, bin_album, bin_track
from .search import SearchResult, search_artist
from .sources import Cancelled
from .tag import audio_length, measure, measured_length, raw_tags
from .text import key as text_key
from .text import move_feat, strip_self_feat
from .timing import (
    ALIGN,
    LISTEN,
    TRANSCRIBE,
    Engines,
    TimingUnavailable,
    capabilities_of,
    coverage,
    language_hint,
    place_by_listening,
    plain_lines,
    release_gpu_memory,
    stamped,
    stays_here,
    with_gaps,
)
from .timing import kind_for as timing_kind
from .treatment import for_album, held_back, says_exceptions, with_exception
from .trim import ORIGINALS, kept_originals, originals_of
from .trim import key as trim_key

log = logging.getLogger(__name__)

EDITABLE_ALBUM = ("album", "albumartist", "year")
EDITABLE_TRACK = ("artist", "title")


def parse_time(value: object) -> float | None:
    """'8', '0:08', '1:02.5' -> seconds; empty -> None. Raises ValueError on nonsense."""
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(p.strip() for p in parts):
        raise ValueError(f"not a time: {text!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    if seconds < 0:
        raise ValueError("times cannot be negative")
    return seconds


@dataclass
class Outcome:
    status: str  # ok | failed | blocked | incomplete | planned | reported | dry | held
    plan: AlbumPlan | None = None
    album_dir: Path | None = None
    message: str = ""

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


TAB_LABELS = {
    "releases": "Releases (official albums and singles)",
    "playlists": "Playlists",
    "ytmusic": "On YouTube Music",
    "folders": "Albums in this folder",
    "posts": "Posts",
}


def may_look_up(cfg: Config, plan: AlbumPlan) -> bool:
    """Whether anything about this album may be asked of lrclib or MusicBrainz (§9, slice 72).

    A private source means no, and it means no for the *question* as much as for the answer: a lookup
    sends a title, a creator and a duration to somebody else's server, and for an album a patron paid
    a creator for, nobody asked for that to happen. The album's owner can say otherwise for that one
    album by setting `"lookups": true` in its plan; nothing sets it for them, and no pass changes it.
    """
    if plan.lookups is not None:
        return bool(plan.lookups)
    return not sources.private_album(plan, cfg)


def refuse_folder(folder: Path, library: Path, itself: bool = False) -> str:
    """Why this folder cannot be taken into that library — or "" when it can (§9, slice 92).

    Module-level so the page, the door and the pass answer the same question: a folder that is the
    library, inside it, or holds it would have the library compared with or adopted into itself.
    """
    folder = folder.expanduser()
    if str(folder) in ("", "."):        # an empty field, which is what `Path("")` comes to
        return "name a folder to take in"
    if not folder.is_absolute():
        return f"{folder} is not an absolute path — name the folder in full"
    if not folder.exists():
        return f"there is nothing at {folder}"
    if not folder.is_dir():
        return f"{folder} is a file, not a folder"
    here, there = library.resolve(), folder.resolve()
    if there == here:
        # `take-in` is the one pass that may be pointed at the library: it takes a collection in
        # **where it stands**, and for somebody whose library *is* their collection that is the case
        return "" if itself else "that is the library itself"
    if here in there.parents:
        if itself:
            return ""
        return f"{folder} is inside the library — a folder is taken in from somewhere else"
    if there in here.parents:
        return f"{folder} holds the library, so taking it in would take the library into itself"
    return ""


def may_send_audio(cfg: Config, plan: AlbumPlan, capability: str = "") -> bool:
    """…and whether this album's **audio** may be handed to a timing provider (§9, slice 75).

    Module-level so the page can ask exactly what the server will answer: a button that offers what
    the server refuses is a worse answer than no button (§9, slice 42), and this is the one question
    where the refusal is about the recording itself.

    **`lookups` does not answer this one** (§9, slice 77). It says a title and a length may be asked
    of a stranger, which is not the same permission as uploading the recording — for one package it
    was read as both, and an album whose owner had allowed a lookup would have had its audio sent to
    a vendor. The album's own `send_audio` is the only thing that says yes here.
    """
    if plan.send_audio is not None:
        return bool(plan.send_audio)
    return not sources.private_album(plan, cfg) or stays_here(cfg, capability)


class Service:
    def __init__(
        self,
        cfg: Config,
        library: Path | None,
        log: Callable[[str], None] = lambda s: None,
        on_plan: Callable[[AlbumPlan], None] = lambda p: None,
        on_track: Callable[[PlanTrack, str], None] = lambda t, what: None,
        yt: sources.Source | None = None,
        mb: MusicBrainz | None = None,
        cancel: threading.Event | None = None,
        lrclib: LyricsAPI | None = None,
        engines: Engines | None = None,
    ) -> None:
        self.cfg = cfg
        self.library = library.expanduser() if library else None
        self.log, self.on_plan, self.on_track = log, on_plan, on_track
        self.cancel = cancel
        # `yt` is the one provider a Service was built with; a plan that belongs to another asks
        # the registry for its own (§9, slice 51). Tests pass a stand-in here, which is why it stays.
        self.yt = yt or sources.get(None, cfg, cancel)
        self._cancel = cancel
        self._mb = mb
        self._lrclib = lrclib
        # Who is holding the graphics card (§9, slice 82). The app's server passes its own holder, so
        # the models stay loaded from one job to the next and its idle window decides when they go; a
        # CLI run gets one of its own and gives it back when the pass ends.
        self.engines = engines or Engines()
        self._owns_engines = engines is None

    def may_look_up(self, plan: AlbumPlan) -> bool:
        """Whether anything about this album may be asked of lrclib or MusicBrainz (§9, slice 72)."""
        return may_look_up(self.cfg, plan)

    def check(self) -> None:
        """Stop here if the job was cancelled (only called where stopping is safe)."""
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    @property
    def mb(self) -> MusicBrainz | None:
        if not self.cfg.musicbrainz:
            return None
        if self._mb is None:
            self._mb = MusicBrainz(default_cache_path())
        return self._mb

    @property
    def lrclib(self) -> LyricsAPI | None:
        if not self.cfg.lyrics:
            return None
        if self._lrclib is None:
            self._lrclib = Lrclib(lyrics_cache_path())
        return self._lrclib

    # -- one source --------------------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        dry: bool = False,
        plan_only: bool = False,
        report_only: bool = False,
        dump: Path | None = None,
    ) -> Outcome:
        """Read a playlist/video, enrich it, merge with the library, then (unless told not to) download."""
        self.check()
        source = self.source_for_address(url)
        self.log(f"reading {self._said(url, source)} …")
        collection = source.collection(url)
        self.check()
        if dump:
            dump.write_text(json.dumps(collection.to_dict(), indent=2, ensure_ascii=False) + "\n")
        # what a folder held that is not a track, and how many copies of one recording it had.
        # Both are the kind of thing a person wants to hear once, while looking at the folder.
        if note := (getattr(source, "captions_note", None) or (lambda: None))():
            # said on a dry run too, because that is where somebody decides whether to pay for it
            self.log(f"  {note}")
        if reader := getattr(source, "ignored", None):
            if left := reader(url):
                self.log("  left alone: " + ", ".join(f"{n} {kind}" for kind, n in sorted(left.items())))
        if extra := sum(max(len(e.copies) - 1, 0) for e in collection.entries):
            self.log(f"  {extra} further cop{'y' if extra == 1 else 'ies'} of "
                     f"{'a recording' if extra == 1 else 'these recordings'} in the same folder, "
                     "kept as candidates")
        if unreadable := collection.unreadable:
            # never classify, merge or rename from a partial view (DESIGN.md §3.8)
            reason = unreadable[0].skipped or "unknown"
            kind = unreadable[0].skipped_kind
            msg = (
                f"{len(unreadable)} of {len(collection.entries)} videos could not be read right now ({reason}). "
                "Nothing was changed — try again later."
            )
            self.log(msg)
            return Outcome("blocked" if kind == Failure.BOT_CHECK else "incomplete", message=msg)

        plan = build_plan(collection, source=source)
        plan.provider = getattr(source, "name", sources.DEFAULT)  # whose collection this was
        plan.own_the_candidates()
        if collection.entries and not plan.tracks:
            # every video unusable for a reason that will not pass (Music Premium only, private,
            # removed): there is no album here, and writing one leaves an empty folder behind
            reasons = Counter(s["reason"] for s in plan.skipped)
            listed = ", ".join(f"{n}× {reason}" for reason, n in reasons.most_common(2))
            msg = f"nothing to download in “{plan.album}”: all {len(collection.entries)} videos are unusable ({listed}). Nothing was written."
            self.log(msg)
            return Outcome("failed", plan, message=msg)

        if not self.may_look_up(plan):
            self.log("  nothing about this album is looked up anywhere: its audio came from a source "
                     "one person paid for (set \"lookups\": true in its plan to change that)")
        # **asked in this order on purpose**: reading `self.mb` builds the client, and building it
        # creates a cache file — for an album nothing may be asked about, even that is more than
        # nothing (R-259, queued; found in the live run's own cache directory)
        if self.may_look_up(plan) and (mb := self.mb):
            stats = enrich(plan, mb, progress=lambda m: (self.check(), self.log(f"  {m}")),
                           source=self.source_for(plan))
            self.log("MusicBrainz: " + ("release matched" if stats["release"] else f"{stats['tracks']}/{stats['looked_up']} tracks matched"))
            # enrichment keeps bracket groups MusicBrainz lacks, which puts a live album's own
            # name back into every track ("Louder Than Hell (Live in Hamburg)") - so the
            # album-wide judgement is made again, on the final titles
            if dropped := drop_album_name(plan.album, plan.tracks):
                self.log(f"  {dropped} track title(s) lost the repeated album name")
        if named := set_single_album_name(plan):  # a single is its song, under whatever name it ended up with
            self.log(f"  the single is named after its track: “{named}”")

        # Intake never writes into an album that is already here. Matching one of its files to a
        # track we already hold is the same-recording question, and that is P52's first rule — so
        # this reports the overlap and stops, rather than deciding it early and quietly.
        if plan.provider == "folder" and (twin := self._named_like(plan)):
            known_dir, existing = twin
            mine = {text_key(t.title) for t in plan.tracks}
            overlap = sum(text_key(t.title) in mine for t in existing.tracks)
            where = known_dir.relative_to(self.library)
            msg = (f"already in the library as {where}: {overlap} of {len(plan.tracks)} titles overlap. "
                   "Nothing was changed — choosing between two copies is not this pass's job.")
            self.log(msg)
            self.on_plan(plan)
            return Outcome("dry" if dry else "held", plan, known_dir, msg)

        if dry or self.library is None:
            # A preview is only worth having if it is the outcome, so it goes through what a real
            # fetch goes through — the merge with what is already there and the harmonisation —
            # and writes nothing. Without the merge it would show a fresh plan for an album whose
            # stored one carries the user's own edits, and promise names the fetch would not write.
            known_dir = None
            if found := (find_plan(self.library, plan.source_id) if self.library and self.library.exists() else None):
                known_dir, existing = found
                fresh_ids = {t.video_id for t in plan.tracks}
                new = sum(t.video_id not in {x.video_id for x in existing.tracks} for t in plan.tracks)
                plan = merge_plans(existing, plan)
                gone = sum(not t.in_source for t in plan.tracks)
                self.log(f"already in the library as {known_dir.relative_to(self.library)}: "
                         f"{len(fresh_ids)} in the source now, {new} new, {gone} no longer there")
            self._settle_artist(plan)  # read-only: a dry run shows the artist a fetch would write
            self.on_plan(plan)
            return Outcome("dry", plan, known_dir)

        if found := find_plan(self.library, plan.source_id):
            old_dir, existing = found
            known = {t.video_id for t in existing.tracks}
            new = sum(t.video_id not in known for t in plan.tracks)
            plan = merge_plans(existing, plan)
            gone = sum(not t.in_source for t in plan.tracks)
            self.log(f"existing album {old_dir.relative_to(self.library)}: {new} new, {gone} no longer in the source")
            if report_only:
                return Outcome("reported", plan, old_dir)
            self._settle_artist(plan)  # before the folder is chosen, or the album stays put
            album_dir = relocate(old_dir, plan, self.library, for_album(self.cfg, plan))
        else:
            if report_only:
                self.log(f"not in the library yet: {plan.folder}")
                return Outcome("reported", plan)
            self._settle_artist(plan)
            album_dir = self.library / plan.folder

        self.check()  # last point before anything on disk changes
        self.on_plan(plan)
        if plan_only:
            save_plan(plan, album_dir)
            return Outcome("planned", plan, album_dir)
        return self.execute(plan, album_dir)

    def _decide_spellings(self) -> dict[str, tuple[str, set[str | None]]]:
        """One spelling per artist key for the whole library, decided before anything is renamed.

        `repair` used to ask the question once per album, against the library *as stored*, so
        an album already visited could not benefit from evidence found later: with three albums
        in three spellings the first pass left two of them and a second pass was needed
        (§9, slice 23). One scan settles every key instead — and one scan is also all it costs, rather
        than one per album. The candidates are what the library holds: every album-level
        spelling with its provenance, plus the spelling an album's own tracks carry where
        `track_spelling`'s guards hold, which is MusicBrainz evidence.
        """
        candidates: dict[str, dict[str, set[str | None]]] = {}
        # **how many albums hold each spelling**, which is the tie-break the alphabet used to win
        # (§9, slice 129). Counted per album, not per candidate: the spelling an album's own tracks
        # carry is MusicBrainz evidence and is a candidate, never a vote.
        held: dict[str, dict[str, int]] = {}
        for _, plan in iter_plans(self.library) if self.library and self.library.exists() else []:
            key = text_key(plan.albumartist)
            for name, source in ((plan.albumartist, plan.provenance.get("albumartist")), (track_spelling(plan), Provenance.MB)):
                if name:
                    candidates.setdefault(key, {}).setdefault(name, set()).add(source)
            held.setdefault(key, {})[plan.albumartist] = held.get(key, {}).get(plan.albumartist, 0) + 1
        decided: dict[str, tuple[str, set[str | None]]] = {}
        self._spelling_counts = held
        self._spelling_basis = {}
        for key, names in candidates.items():
            counts = held.get(key, {})
            best = min(names, key=lambda n: spelling_rank(n, names[n], counts.get(n, 0)))
            decided[key] = (best, names[best])
            self._spelling_basis[key] = spelling_basis(best, names[best], names, counts)
        return decided

    def _apply_spelling(self, plan: AlbumPlan, decided: dict[str, tuple[str, set[str | None]]]) -> None:
        """Give this album the spelling the library decided on for its artist."""
        if plan.provenance.get("albumartist") == Provenance.USER:
            return  # theirs, and it still counted as a candidate for everyone else
        chosen = decided.get(text_key(plan.albumartist))
        if not chosen or chosen[0] == plan.albumartist:
            return
        self.log(f"artist spelled '{chosen[0]}' elsewhere in the library — using that")
        self._adopt(plan, chosen[0], chosen[1])

    def one_spelling_per_artist(self, plan: AlbumPlan,
                                decided: dict[str, tuple[str, set[str | None]]]) -> str:
        """Give this album the library's spelling where the difference is **case alone**.

        Outside `tidy_adopted_tags`, and that is the whole point (§9, slice 126). `_apply_spelling`
        is gated behind it — rightly, since reading a folder's tags as a video title's is what slice
        111 forbids — so an adopted album kept its own spelling and the library grew two artist
        folders for one artist. Measured on the share after the merge: `Die Legende Von Nord` holds
        four albums and `Die Legende von Nord` holds `Angst im Dunkeln`, whose own tags say `von`.
        On a case-sensitive filesystem those are two artists, two folders and two places to look.

        Only case: `Die Legende von Nord` against `Die Legende Von Nord`, never `JBO` against
        `J.B.O.`. Nothing of what the tag *says* changes, only how it is cased — and the album's own
        spelling still counted as a candidate for the decision, so the one the library keeps may well
        be this album's. A spelling the user chose is never touched.
        """
        if plan.provenance.get("albumartist") == Provenance.USER:
            return ""
        chosen = decided.get(text_key(plan.albumartist))
        if not chosen or chosen[0] == plan.albumartist:
            return ""
        if chosen[0].casefold() != plan.albumartist.casefold():
            return ""       # a different name, not a different casing: that is `_apply_spelling`'s
        was, plan.albumartist = plan.albumartist, chosen[0]
        plan.auto["albumartist"] = chosen[0]
        basis = getattr(self, "_spelling_basis", {}).get(text_key(chosen[0]), "alphabet")
        # **a spelling chosen by count says so, in the plan** (R-479, point 1). The user:
        # "such stuff should follow MusicBrainz, not numbers" — so the count is recorded as the weak
        # ground it is, and a later pass that asks MusicBrainz replaces it without argument.
        if basis == "count":
            plan.provenance["albumartist"] = Provenance.SOURCE_TAGS
            plan.adopted = dict(plan.adopted or {})
            plan.adopted["spelling"] = "by count; MusicBrainz not asked"
        elif isinstance(plan.adopted, dict):
            plan.adopted.pop("spelling", None)
        return f"{was!r} -> {chosen[0]!r} ({basis})"

    def _settle_artist(self, plan: AlbumPlan) -> None:
        """The artist this fetch writes: the album's own tracks first, then the library's spelling.

        A fetch renames only the album it is fetching. So when the library already holds a
        spelling for this artist key, the incoming album adopts it — even when it arrives with
        better evidence, because upgrading the other albums is `noaap repair`'s job, not a
        side effect of fetching something. When the newcomer *is* the better evidence, one line
        says so and names both spellings. The cost, accepted: an older spelling can stand until
        repair runs. What it buys is one folder per artist (DESIGN.md §9, slice 23).
        """
        self._adopt_track_spelling(plan)
        if plan.provenance.get("albumartist") == Provenance.USER or not self.library or not self.library.exists():
            return  # a spelling chosen for *this* album wins for this album, second folder or not
        seen = self._spellings(plan)
        if not seen:
            return  # the library knows this artist under no other spelling
        theirs = min(seen, key=lambda n: spelling_rank(n, seen[n]))
        if theirs == plan.albumartist:
            return
        ours = {plan.provenance.get("albumartist")}
        if spelling_rank(plan.albumartist, ours) < spelling_rank(theirs, seen[theirs]):
            self.log(
                f"this album spells the artist '{plan.albumartist}', the library '{theirs}' — keeping "
                f"'{theirs}' so there is one folder; 'noaap repair' (or “Repair library” in the web UI) "
                f"unifies them on the better spelling"
            )
        else:
            self.log(f"artist spelled '{theirs}' elsewhere in the library — using that")
        self._adopt(plan, theirs, seen[theirs])

    def _adopt(self, plan: AlbumPlan, name: str, sources: set[str | None]) -> None:
        """Take a spelling from elsewhere in the library, with the evidence it really has.

        Not the evidence *this* album had: a spelling adopted while the album's own came from
        MusicBrainz used to keep the `mb` marker, so a shouted name inherited a confirmation
        MusicBrainz never gave — and `repair` then converged on the shouting (§9, slice 23). And never
        `user`, which means "the user chose this for *this* album" and would freeze it.
        """
        plan.albumartist = plan.auto["albumartist"] = name
        for source in (Provenance.MB, Provenance.SOURCE_TAGS, Provenance.COLLECTION):
            if source in sources:
                plan.provenance["albumartist"] = source
                break
        else:
            plan.provenance["albumartist"] = Provenance.SOURCE_TITLE
        refresh_derived(plan)

    def _spellings(self, plan: AlbumPlan) -> dict[str, set[str | None]]:
        """Every spelling the *rest* of the library has for this artist key, and where each came from."""
        key = text_key(plan.albumartist)
        seen: dict[str, set[str | None]] = {}
        for _, other in iter_plans(self.library) if self.library else []:
            if other.source_id != plan.source_id and text_key(other.albumartist) == key:
                seen.setdefault(other.albumartist, set()).add(other.provenance.get("albumartist"))
        return seen

    def _adopt_track_spelling(self, plan: AlbumPlan) -> None:
        """An album spelled unlike its own tracks: MusicBrainz credited the tracks, believe them.

        The release credit and the track credits are separate fields in MusicBrainz and do
        disagree ("LORD OF THE LOST" on the release, "Lord of the Lost" on every track). The
        album is made consistent with itself before the library is consulted, so what the
        library then weighs — and what `repair` later sees — is the better spelling.
        """
        common = track_spelling(plan)
        if not common:
            return
        if common == plan.albumartist:
            # already spelled as the tracks are — but possibly without saying where that came
            # from. `repair`'s own "use the most common track artist" rule renames without a
            # marker, and an unmarked spelling loses a tie to any other mixed-case spelling in
            # the library, alphabetically, which is a coin flip.
            plan.provenance["albumartist"] = Provenance.MB
            return
        self.log(f"the tracks are credited '{common}', the album '{plan.albumartist}' — using the tracks' spelling")
        plan.albumartist = plan.auto["albumartist"] = common
        # and it carries the tracks' evidence: MusicBrainz credited them, which is the whole
        # reason to believe them. Left at the album's old marker, this spelling loses a tie to
        # any other mixed-case spelling in the library — alphabetically, which is a coin flip.
        plan.provenance["albumartist"] = Provenance.MB
        refresh_derived(plan)  # or a new album keeps the folder of the spelling just dropped

    def _run(self, plan: AlbumPlan, album_dir: Path, **kw: Any) -> AlbumPlan:
        """Every pass that writes files goes through here, so every pass brings the album to the
        library's state (§9, slice 100) and writes carefully where the files are not ours to lose
        (§9, slice 99). The treatment is the settings, minus this album's own exceptions."""
        return run(plan, album_dir, self.source_for(plan), want=for_album(self.cfg, plan),
                   careful=bool(plan.adopted), track_source=self._track_source(plan),
                   on_track=self.on_track, check=self.check, **kw)

    def execute(self, plan: AlbumPlan, album_dir: Path) -> Outcome:
        todo = sum(t.state != "done" and t.in_source for t in plan.tracks)
        self.log(f"downloading {todo} of {len(plan.tracks)} tracks into {album_dir}")
        self._run(plan, album_dir, say=self.log,
                  lyrics=self.lrclib if self.may_look_up(plan) else None)
        failed = [t for t in plan.tracks if t.state != "done" and t.in_source]
        self.log(f"{len(plan.tracks) - len(failed)}/{len(plan.tracks)} tracks done" + (f", {len(failed)} not yet — run again to retry" if failed else ""))
        if any(t.error_kind == Failure.BOT_CHECK for t in failed):
            blocking = next(t.error for t in failed if t.error_kind == Failure.BOT_CHECK)
            return Outcome("blocked", plan, album_dir, blocking)
        return Outcome("failed" if failed else "ok", plan, album_dir)

    def download_existing(self, album_dir: Path) -> Outcome:
        """Run an (edited) plan in an album folder: rename, retag, fetch what is missing."""
        plan = load_plan(album_dir)
        if not plan:
            return Outcome("failed", message=f"no plan in {album_dir}")
        library = album_dir.resolve().parents[1]  # <library>/<artist>/<album>
        album_dir = relocate(album_dir, plan, library, for_album(self.cfg, plan))
        self.on_plan(plan)
        return self.execute(plan, album_dir)

    # -- many sources ------------------------------------------------------------------

    def fetch_many(self, refs: list[SourceRef], dry: bool = False) -> list[Outcome]:
        outcomes: list[Outcome] = []
        for i, ref in enumerate(refs, 1):
            self.log(f"=== [{i}/{len(refs)}] {ref.title}")
            outcomes.append(self._guarded(lambda: self.fetch(ref.url, dry=dry)))
            if outcomes[-1].blocked:
                self.log(f"stopping: YouTube is blocking requests; {len(refs) - i} not fetched")
                break
        return outcomes

    def reread(self, album_dir: Path) -> Outcome:
        """Read an adopted album's own folder again and bring its plan up to date. **Copies nothing.**

        This is not `fetch`. A fetch on an album's own folder takes the folder as a *source* and
        will copy from it — and because the provider names an entry by its best copy, a better file
        dropped into the folder changes that entry's ref, so the merge reads it as a new track and
        the download half then writes the new file over the old one's name. Measured, not feared:
        one file added to an adopted album did exactly that (§9, slice 59).

        So the refs are aligned first. A fresh entry that shares **any** copy with a track we
        already have *is* that track: it keeps its ref and its file name, and the new file joins it
        as a candidate — which is what a second copy of a recording has been since slice 50, and
        choosing between them stays `merge`'s job and a person's.
        """
        plan = load_plan(album_dir)
        if not plan:
            return Outcome("failed", message=f"no plan in {album_dir}")
        if Path(plan.source_id).resolve() != album_dir.resolve():
            # **only an album that is its own source is re-read this way.** One taken in from an
            # intake folder still belongs to that folder: its refs name the files over there, so
            # reading it here would see every track as new and double the album — measured, live,
            # on an imported album that went from 13 tracks to 26 (§9, slice 59). What re-checks
            # that one against where it came from is `update`.
            self.log(f"{plan.albumartist} — {plan.album} came from somewhere else; `update` re-checks it")
            return Outcome("held", plan, album_dir, "not this album's own folder")
        source = sources.for_plan(plan, self.cfg, self.cancel)
        try:
            collection = source.collection(str(album_dir))
        except sources.SourceError as e:
            return Outcome("failed", plan, album_dir, f"cannot read the folder: {e}")
        fresh = build_plan(collection, source=source)
        fresh.provider = getattr(source, "name", sources.DEFAULT)
        fresh.own_the_candidates()

        mine = {ref: track for track in plan.tracks
                for ref in [track.video_id, *(c.ref for c in track.candidates)]}
        for track in fresh.tracks:
            refs = [track.video_id, *(c.ref for c in track.candidates)]
            if (known := next((mine[r] for r in refs if r in mine), None)) is None:
                continue  # a track this album did not have: it arrives as itself
            track.video_id = known.video_id  # the same recording, whatever file is best today
            track.sync_candidates()

        merged = merge_plans(plan, fresh)
        merged.keep_names, merged.keep_tags = plan.keep_names, plan.keep_tags
        merged.adopted = plan.adopted
        # a second file of a recording we already have is a **candidate**, not a replacement: the
        # merge keeps the track it knows, and this carries what the folder now offers beside it.
        # Choosing between two copies is `merge`'s question and a person's (§9, slices 50 and 54).
        by_ref = {track.video_id: track for track in merged.tracks}
        for track in fresh.tracks:
            if (known := by_ref.get(track.video_id)) is None:
                continue
            for copy in track.candidates:
                if not known.candidate(copy.ref):
                    known.candidates.append(replace(copy))
        # **a new file in an adopted album is already here.** The merge appends it as a fresh
        # track, and a fresh plan derives its name by noaap's scheme — which in a library that
        # keeps its own names is both wrong and dangerous: the derived name collides with an
        # existing file and the track, being `pending`, would be fetched and written over it.
        # Found live on a real album (§9, slice 59). It is adopted exactly as every other track of
        # this album was: the file it came from, under the name its owner gave it.
        # where each file is comes from the provider, never off the ref (§9, slice 61) — and it is
        # what makes a file dropped into a **disc sub-folder** arrive as that disc's track instead of
        # staying pending and being copied into the album root under a name of ours.
        found = {entry.video_id: entry.where for entry in collection.entries}
        for track in merged.tracks:
            if track.state == "done" or not track.video_id:
                continue
            if not (where := found.get(track.video_id)) or not (here := album_dir / where).is_file():
                continue
            track.filename, track.state = where, "done"
            track.adopted_name = track.adopted_name or where
            track.adopted_tags = track.adopted_tags if track.adopted_tags is not None \
                else dict(raw_tags(here))
        save_plan(merged, album_dir)
        added = sum(t.video_id not in {x.video_id for x in plan.tracks} for t in merged.tracks)
        gone = sum(not t.in_source for t in merged.tracks)
        self.log(f"{merged.albumartist} — {merged.album}: {added} new, {gone} no longer in the folder")
        return Outcome("ok", merged, album_dir)

    def update_all(self, report_only: bool = False, deep: bool = False, artist: str | None = None) -> list[Outcome]:
        """Check every album (or one artist's). Unchanged, complete albums cost one request."""
        albums = list(iter_plans(self.library)) if self.library and self.library.exists() else []
        if artist:
            albums = [(d, p) for d, p in albums if p.albumartist.casefold() == artist.casefold()]
        if not albums:
            self.log(f"no albums{f' by {artist}' if artist else ''} in {self.library}")
        outcomes: list[Outcome] = []
        skipped = lengths = 0
        for i, (album_dir, plan) in enumerate(albums, 1):
            self.log(f"=== [{i}/{len(albums)}] {plan.albumartist} — {plan.album}")
            self._look_at_the_discs(plan, album_dir, report_only)
            if not deep and (unchanged := self._unchanged(plan)):
                skipped += 1
                self.log(f"  unchanged ({unchanged}) — nothing to do")
                outcomes.append(Outcome("ok", plan, album_dir, "unchanged"))
                continue
            # an album it touches gets its lengths, before the fetch reads the plan back off disk
            if filled := self.measure_lengths(plan, album_dir, dry_run=report_only):
                self.log(f"  {filled} track(s) {'would be' if report_only else 'were'} measured")
                lengths += filled
                if not report_only:
                    save_plan(plan, album_dir)
            outcomes.append(self._guarded(lambda: self.fetch(plan.source_url, report_only=report_only)))
            if outcomes[-1].blocked:
                self.log(f"stopping: YouTube is blocking requests; {len(albums) - i} album(s) not checked")
                break
        if skipped:
            self.log(f"{skipped} of {len(albums)} albums were unchanged")
        if lengths:
            self.log(f"{lengths} track(s) {'would get' if report_only else 'got'} the length of their file")
        return outcomes

    def measure_lengths(self, plan: AlbumPlan, album_dir: Path, *, dry_run: bool = False) -> int:
        """Give every finished track with a file the length of that file (§9, slice 56).

        The measuring was never missing — `run` does it for a track it has just written, and for one
        it finds without a length. What was missing is a pass that *reaches* those tracks: `repair`
        skips an album whose names are already right, before the point where anything is measured,
        and in a tidy library that is nearly every album. **574 of the reference library's 3946
        tracks had a file, a length nobody had ever asked for, and therefore no length chip and no
        near-miss check** — not because the file would not answer, but because nothing asked.

        **And a length whose origin nobody recorded is half a length** (§9, slice 77). `run` measures
        a track that has none and then never again, so `file_length_by` — added after most of these
        plans were written — stayed empty on every track that already had a number: 4583 of one
        library's tracks against 559 with it. Those are filled here too, but **only from the header**:
        a header that answers costs nothing and says "header"; one that does not would need a decode
        per track, and a pass that quietly decodes thousands of files is not a tidy-up. Their length
        is left exactly as it was — this fills in where it came from, it does not second-guess it.

        Returns how many tracks were given one; with `dry_run`, how many would be asked.
        """
        filled = 0
        for track in plan.tracks:
            if track.state != "done" or not track.filename:
                continue
            wanted = not track.file_length or not track.file_length_by
            if not wanted:
                continue
            audio = _inside(album_dir, track.filename)
            if audio is None or not audio.is_file():
                continue  # a plan that names a file that is not there is `repair`'s other business
            if dry_run:
                filled += 1
                continue
            if track.file_length:
                # The number is already there and **is not touched**: an old rule of this pass, and
                # a good one — a length may have been set by a trim or by whoever edited the plan.
                # Only its origin is filled, and only when the header both answers and agrees with
                # what the plan holds. A header that says something else does not make the plan's
                # number the header's, so the origin stays empty and says so by being empty.
                said = audio_length(audio)
                if said is not None and abs(said - track.file_length) <= 0.05:
                    track.file_length_by = "header"
                    filled += 1
                continue
            seconds, how = measure(audio)
            if seconds is None:
                continue  # unknown stays unknown: a zero is not a length (§9, slice 53)
            track.file_length, track.file_length_by = seconds, how
            filled += 1
        return filled

    def _unchanged(self, plan: AlbumPlan) -> str | None:
        """One cheap request: is this album still exactly what the source lists, and complete?

        Returns a short reason when it can be skipped, else None (then it is read in full).
        """
        known = plan.source_state or {}
        if not known.get("ids"):
            return None  # never recorded (older album): read it properly
        waiting = [t for t in plan.tracks if t.in_source and t.state != "done" and not t.error_kind]
        if waiting:
            return None  # something is still missing here
        try:
            now = self.source_for(plan).changed(plan.source_url)
        except Cancelled:
            raise
        except Exception as e:
            self.log(f"  could not check quickly ({e}); reading it in full")
            return None
        if not now or now["ids"] != known["ids"]:
            return None
        if now.get("modified") != known.get("modified"):
            return None  # the playlist itself changed (or we never recorded a date): read it once
        return f"{len(now['ids'])} videos, unchanged since {now.get('modified') or 'last time'}"

    def _guarded(self, action: Callable[[], Outcome]) -> Outcome:
        try:
            return action()
        except Cancelled:
            raise  # the user's cancel ends the whole job, not just this source
        except Exception as e:  # one broken source must not stop the others
            log.debug("source failed", exc_info=True)
            self.log(f"  failed: {e}")
            return Outcome("failed", message=str(e))

    # -- discovery ---------------------------------------------------------------------

    def channel(self, url: str) -> list[tuple[str, list[SourceRef]]]:
        source = self.source_for_address(url)
        if not sources.can(source, sources.LISTING):
            raise sources.NotSupported(
                f"{getattr(source, 'name', 'this source')} cannot list what an owner publishes — "
                "give the address of one album instead.")
        self.log(f"reading {self._said(url, source)} …")
        refs = source.listing(url)
        # **every tab a provider minted is shown.** This was a table of the three tabs YouTube and
        # the folder source use, and a provider that named its own — Patreon's `posts` — had its whole
        # listing dropped on the floor: the campaign read fine and the CLI said "this channel has no
        # releases or playlists" (§9, slice 71). Known tabs keep their order and their sentence;
        # anything else is shown under its own name rather than not at all.
        seen = [r.tab for r in refs]
        order = [t for t in TAB_LABELS if t in seen] + sorted(set(seen) - set(TAB_LABELS))
        groups = [(TAB_LABELS.get(tab) or tab.replace("_", " ").capitalize(),
                   [r for r in refs if r.tab == tab]) for tab in order]
        return [g for g in groups if g[1]]

    def search(self, artist: str) -> SearchResult:
        """Find an artist's albums by name — from a provider that can (§9, slice 57)."""
        self._must(sources.SEARCH, "search by name", "paste an address instead")
        self.log(f"searching {getattr(self.yt, 'name', 'the source')} for {artist!r} …")
        return search_artist(self.yt, artist, self.mb)

    def _must(self, capability: str, what: str, instead: str) -> None:
        """Refuse with the names of the providers that *can*, which is the useful half."""
        if sources.can(self.yt, capability):
            return
        able = sorted(n for n in sources.known()
                      if sources.can(sources.get(n, self.cfg), capability))
        mine = getattr(self.yt, "name", "this source")
        raise sources.NotSupported(
            f"{mine} cannot {what} — {instead}."
            + (f" These can: {', '.join(able)}." if able else ""))

    def library_source_ids(self) -> set[str]:
        if not self.library or not self.library.exists():
            return set()
        return {p.source_id for _, p in iter_plans(self.library)}

    # -- removing tracks that left the source ------------------------------------------------

    def prune(self, album_dir: Path) -> Outcome:
        """Delete the tracks the last fetch found no longer in the source; retag the rest."""
        plan = load_plan(album_dir)
        if not plan:
            return Outcome("failed", message=f"no plan in {album_dir}")
        gone = [t for t in plan.tracks if not t.in_source]
        if not gone:
            self.log("nothing to remove: every track is still in the source")
            return Outcome("ok", plan, album_dir)
        for t in gone:
            path = _inside(album_dir, t.filename)
            entry = self._bin(album_dir, plan, t, PRUNED, audio=path)
            self.log(f"moved {t.number:02d} {t.artist} - {t.title} to the recycle bin"
                     + (f" ({entry.name})" if entry else " — nothing was on disk")
                     + ("" if path else " (unsafe file name ignored)"))
        plan.tracks = [t for t in plan.tracks if t.in_source]
        # Close the gap the removed tracks leave — on every album, not only single-disc ones: for an
        # album noaap fetched, the position in the source is the only thing a number ever was, and
        # what a user order protects is the arrangement, which counting the discs off in their own
        # order keeps exactly.
        # **Unless the source states its numbers** (R-375). Then the files are the authority and a
        # player showing 1, 2, 4 shows the truth: the owner's disc is missing track 3. Closing that
        # gap writes a wrong position into every file after it, which is R-372 by another route.
        if not states_numbers(plan):
            arrange(plan)
        save_plan(plan, album_dir)
        return self.execute(plan, album_dir)  # renames/retags only (tracktotal changed)

    # -- lyrics ----------------------------------------------------------------------------

    def fetch_lyrics(self, refetch: bool = False, artist: str | None = None, source_id: str | None = None) -> list[Outcome]:
        """Look up what is missing, write the `.lrc` sidecars and the tags. Downloads nothing.

        Only tracks that were never looked at are asked for, so running this twice costs
        nothing; `refetch` asks again for all of them (but never for the user's own lyrics).
        """
        api = self.lrclib
        if api is None:
            self.log("lyrics are switched off — turn them on with: noaap config --lyrics on")
            return []
        albums = list(iter_plans(self.library)) if self.library and self.library.exists() else []
        if artist:
            albums = [(d, p) for d, p in albums if p.albumartist.casefold() == artist.casefold()]
        if source_id:
            albums = [(d, p) for d, p in albums if p.source_id == source_id]
        if kept := [p for _, p in albums if not self.may_look_up(p)]:
            # said, not silently skipped: a user who asked for lyrics is owed the reason there are none
            self.log(f"{len(kept)} album(s) are not looked up anywhere: their audio came from a source "
                     "one person paid for")
            albums = [(d, p) for d, p in albums if self.may_look_up(p)]
        outcomes: list[Outcome] = []
        for i, (album_dir, plan) in enumerate(albums, 1):
            self.check()
            if refetch:
                for t in plan.tracks:
                    if not user_owns(album_dir, t):  # a mark with no file behind it protects nothing
                        t.lyrics = None  # lyrics_id stays: it is how a sidecar is recognised as ours
            # a deleted sidecar is work too: the pass has to drop the tag and the status with it
            todo = [t for t in plan.tracks if t.state == "done" and (t.lyrics is None or sidecar_lost(album_dir, t))]
            if not todo:
                continue
            self.log(f"=== [{i}/{len(albums)}] {plan.albumartist} — {plan.album}: {len(todo)} track(s) to look at")
            outcomes.append(self._guarded(lambda: self._lyrics_pass(plan, album_dir, api)))
        counts = Counter(t.lyrics or "not looked up" for _, plan in albums for t in plan.tracks if t.state == "done")
        self.log("lyrics: " + (", ".join(f"{n} {what}" for what, n in counts.most_common()) or "no tracks"))
        return outcomes

    def save_lyrics(self, source_id: str, video_id: str, text: str, timed_by: str = "",
                    words_by: str = "", checked: dict[str, str] | None = None) -> Outcome:
        """Write the words a user typed beside one track — or clear them — and retag it.

        This is the one door into the ownership contract from the UI side: it does by hand what
        `reconcile` does when it finds an edited file (DESIGN.md §9, slice 21, §9, slice 26). Nothing is looked
        up, so a user is never told their words were replaced by lrclib's.
        """
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return Outcome("failed", message="no such track in this album")
        if track.state != "done":
            return Outcome("failed", message=f"{track.title}: there is no file yet to put lyrics beside")
        if body := text.strip():
            # **words that came with the recording stay theirs, whatever the request says** (§9,
            # slice 74, R-258 defect 1). This set `USER` without looking, so one save from the editor
            # turned a creator's captions into the user's own words — and, being the user's, they
            # went straight into the audio file's tag. The page withholds the claim control; that is
            # a courtesy, and this is the rule. Correcting a line of somebody else's writing is not
            # authorship of it, and no request may say otherwise.
            theirs = track.provenance.get("lyrics") == Provenance.SOURCE
            write_sidecar(album_dir, track, body)  # records lyrics_sha as the bytes it wrote
            track.lyrics = status_of(body)
            if not theirs:
                track.provenance["lyrics"] = Provenance.USER
                # the words stay the user's; the *clock* may be a machine's, and says so (§9, slice 36),
                # and so does a draft nobody has rewritten yet (§9, slice 37)
                track.lyrics_timed_by = timed_by or None
                track.lyrics_words_by = words_by or None
            # kept beside the clock, not instead of it: this is the evidence, the clock is the claim
            track.lyrics_checked = dict(checked) if checked else None
            if theirs:
                self.log(f"saved your corrections to {track.lyrics_words_by}'s captions for "
                         f"{track.title} — they are still theirs, and stay beside the file")
            else:
                self.log(f"wrote your lyrics for {track.title} ({track.lyrics})"
                         + (f", drafted by {words_by}" if words_by else "")
                         + (f", timed by {timed_by}" if timed_by else ""))
        else:
            # empty is a clear, not an empty file — and it gives the mark up with the words, so
            # `--refetch` may bring lrclib's back, exactly as deleting the file by hand does
            # a clear gives the mark up with the words — including a source's, because what is gone
            # is gone: whatever somebody writes next is their own, from nothing (§9, slice 74)
            remove_sidecar(album_dir, track.filename)
            track.lyrics, track.lyrics_sha = "none", None
            track.lyrics_timed_by = track.lyrics_words_by = None
            track.provenance.pop("lyrics", None)
            self.log(f"removed the lyrics of {track.title}")
        save_plan(plan, album_dir)
        # retag through the ordinary pass, with no lyrics client: it rewrites the LYRICS tag from
        # the sidecar as every pass does, and downloads nothing
        self._run(plan, album_dir, download=False)
        save_plan(plan, album_dir)
        return Outcome("ok", plan, album_dir)

    def publish_lyrics(self, source_id: str, video_id: str) -> Outcome:
        """Give one track's words back to LRCLIB (§9, slice 42, backlog 19).

        The one thing in noaap that makes something **public and irrevocable**, so it is the one
        thing that asks the most before doing it: the words must be the user's own (not lrclib's,
        not a machine's draft), timed, and different from whatever lrclib already holds for this
        track. The page asks a second time, in a confirm that names everything that leaves.

        What is sent is the track as it is *here*: the titles the user sees and the **file's** own
        length, because that is what the timestamps belong to (§9, slice 35).
        """
        api = self.lrclib
        if api is None or not hasattr(api, "publish"):
            return Outcome("failed", message="lyrics are switched off — turn them on with: noaap config --lyrics on")
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return Outcome("failed", message="no such track in this album")
        text = (read_sidecar(album_dir, track) or "").strip()
        if not text:
            return Outcome("failed", message=f"{track.title}: there are no words beside this track")
        if refused := publishable(track, text, self.their_words(track)):
            return Outcome("failed", message=f"{track.title}: {refused}")
        length = track.file_length or track.duration or 0.0
        if not length:
            return Outcome("failed", message=f"{track.title}: the length of the file is not known yet")
        self.log(f"publishing {track.artist} — {track.title} to lrclib "
                 f"({len(text.splitlines())} lines, {length:.0f} s) — this is public and cannot be undone")
        try:
            api.publish(track_name=track.title, artist_name=track.artist,
                        album_name=plan.album or "", duration=length,
                        plain=plain_text(text), synced=text, log_to=self.log)
        except LyricsError as e:
            # nothing on disk changes: the plan is not written, so the button is still there
            return Outcome("failed", message=str(e))
        track.lyrics_published = {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                                  "sha": sent_sha(text)}
        save_plan(plan, album_dir)
        self.log(f"published {track.title} — thank you: the next person looking for this song finds it")
        return Outcome("ok", message=f"{track.title}: published to lrclib", album_dir=album_dir, plan=plan)

    def their_words(self, track: PlanTrack) -> str | None:
        """What lrclib already holds for this track, if we know which entry it is.

        Only from the cache: this is asked to decide whether a *button* is offered, and a lookup
        per track per page would be a lot of traffic for a question nobody asked yet.
        """
        api = self.lrclib
        if not (track.lyrics_id and api and hasattr(api, "cached_by_id")):
            return None
        held = api.cached_by_id(track.lyrics_id)
        return held.text if held else None

    def align_lyrics(self, source_id: str, video_id: str, text: str,
                     method: str = ALIGN) -> dict[str, Any]:
        """Put the words the editor is holding onto this track's clock. **Writes nothing** (§9, slice 36).

        The words come from the page, not from the disk, because the user may have just typed them;
        what comes back is a proposal the editor shows and the user saves — or does not. That is
        what keeps the ownership contract (§9, slice 21) out of this entirely: the only door to the disk is
        still `save_lyrics`, with the user's hand on it.
        """
        found = self.find_album(source_id)
        if not found:
            raise ValueError(f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            raise ValueError("no such track in this album")
        if track.state != "done":
            raise ValueError(f"{track.title}: there is no file to align against yet")
        lines = plain_lines(text)
        if not lines:
            raise ValueError("there are no words to place")

        if method not in (ALIGN, LISTEN):
            raise ValueError(f"unknown way of placing words: {method!r}")
        audio = album_dir / track.filename
        self._audio_may_be_sent(plan, method, f"{'align' if method == ALIGN else 'listen for'} words")
        engine = self._timing(method, "align words" if method == ALIGN else "listen for the words")
        # asked before anything else is said, so that "this is running on the processor" is the job's
        # first line and not a footnote under a minute of waiting (§9, slice 82)
        where = self._where_it_runs(engine, method)
        if method == LISTEN:
            # **listen first, then match** (§9, slice 83). The provider hears the recording; which of
            # the given lines were actually sung is decided in the core, over words, by nobody's model.
            self.log(f"listening to {track.title} with {engine.name} for {len(lines)} lines "
                     f"· {_minutes(audio)} of audio")
            # **the words say what language they are in** (§9, slice 83): a transcriber left to detect
            # it for itself wrote 27 words of Russian boilerplate over a four-minute German song,
            # and we are holding the lyric. Where the words cannot say, it detects as before.
            hint = language_hint(lines)
            with self._what_it_listens_to(audio) as listen_to:
                heard = engine.heard(listen_to, language=hint, check=self.check, **where)
            self.log(f"heard {len(heard.timed)} words with a time")
            timed = place_by_listening(lines, heard)
        else:
            self.log(f"aligning {len(lines)} lines of {track.title} with {engine.name} "
                     f"· {_minutes(audio)} of audio")
            timed = engine.align(audio, lines, check=self.check, **where)
        placed = len(lines) - len(timed.unplaced)
        for said in (timed.parameters.get("not_heard") or "").split("; "):
            if said:
                self.log(f"  {said}")
        self.log(f"placed {placed}/{len(lines)} lines · {timed.by}"
                 + (f" · {len(timed.unplaced)} left unplaced" if timed.unplaced else ""))
        return {"timed": timed.to_dict(), "by": timed.by, "placed": placed, "lines": len(lines),
                "method": timed.method or ALIGN}

    def draft_lyrics(self, source_id: str, video_id: str) -> dict[str, Any]:
        """Ask a provider what it hears, for a track that has no words at all. **Writes nothing.**

        A draft, and labelled as one everywhere it appears: the spike measured transcription as the
        weaker half of the job — three quarters of a clean song's lines, half of a harsh one's — so
        this is a starting point for someone who would otherwise face an empty editor (§9, slice 37).
        """
        found = self.find_album(source_id)
        if not found:
            raise ValueError(f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            raise ValueError("no such track in this album")
        if track.state != "done":
            raise ValueError(f"{track.title}: there is no file to listen to yet")
        if track.lyrics in ("synced", "plain"):
            # never offered on words that exist, and refused if asked anyway: a draft would
            # overwrite somebody's work, and LRCLIB's entry is better than a guess
            raise ValueError(f"{track.title} already has words — a draft is only for a track that has none")

        self._audio_may_be_sent(plan, TRANSCRIBE, "derive words")
        engine = self._timing(TRANSCRIBE, "derive words")
        where = self._where_it_runs(engine, TRANSCRIBE)
        audio = album_dir / track.filename
        # A transcriber hears far more of a song with the band taken off it — measured, and by a
        # lot (§9, slice 45) — and where the transcriber is somebody else's computer, the isolated voice
        # is also less of the record to send. Where no separator is installed this is the mixed
        # track, exactly as before.
        # imported here and nowhere else, so an installation with no timing extra still never
        # touches this module (§9, slice 36)
        from .timing_local import separated_voice

        with tempfile.TemporaryDirectory(prefix="noaap-voice-") as tmp:
            voice = separated_voice(audio, Path(tmp) / "voice.wav", self.log, self.check)
            heard = "the separated voice" if voice else "the mixed track"
            self.log(f"asking {engine.name} to draft the words of {track.title} · "
                     f"{_minutes(audio)} of audio · listening to {heard}")
            timed = engine.transcribe(voice or audio, check=self.check, **where)
        timed.parameters["heard"] = heard
        # where the machine heard nothing for a long stretch, the draft says so rather than letting
        # the next line jump a minute — and the notice counts what it actually covered (§9, slice 45)
        length = track.file_length or track.duration
        timed.lines = with_gaps(timed.lines, length)
        timed.parameters.update(coverage(timed.lines, length))
        text = "\n".join(line.text for line in timed.lines)
        heard = float(timed.parameters.get("covered", 0) or 0)
        self.log(f"drafted {len(timed.lines)} lines · {timed.by} — words for {heard:.0f} s of "
                 f"{length:.0f} s of audio, {timed.parameters.get('gaps', '0')} gaps — a machine's guess, check it"
                 if length else f"drafted {len(timed.lines)} lines · {timed.by} — a machine's guess, check it")
        return {"timed": timed.to_dict(), "by": timed.by, "lines": len(timed.lines),
                "placed": len(timed.lines) - len(timed.unplaced), "text": text}

    def _named_like(self, plan: AlbumPlan) -> tuple[Path, AlbumPlan] | None:
        """An album already in the library with this artist and this name, from somewhere else."""
        if not self.library or not self.library.exists():
            return None
        want = (text_key(plan.albumartist), text_key(plan.album))
        for album_dir, existing in iter_plans(self.library):
            if existing.source_id != plan.source_id and (text_key(existing.albumartist), text_key(existing.album)) == want:
                return album_dir, existing
        return None

    def source_for_address(self, address: str):
        """Whose address this is. The core cannot tell one from another; each provider can.

        This Service's own provider is asked first, so a test's stand-in keeps answering for the
        addresses it was given. An address nobody claims still goes to the default, which then
        says why it cannot read it — better than a message about providers (§9, slice 53).
        """
        asked = getattr(self.yt, "handles", None)
        if asked is None or asked(address):
            # a provider that cannot be asked *is* the answer: a Service built with one specific
            # provider was built with it on purpose, and a test's stand-in must not be replaced
            # by the real thing behind its back
            return self.yt
        for name in sources.known():
            found = sources.get(name, self.cfg, self._cancel)
            if found.handles(address):
                return found
        return self.yt

    @staticmethod
    def _said(address: str, source: Any) -> str:
        """An address as it is worth reading in a log line: a URL entire, a path by its last two
        parts. The whole path is the user's own directory tree and belongs in one place only —
        the source panel of a single track (§9, slice 53)."""
        if getattr(source, "name", None) != "folder":
            return address
        parts = Path(address).parts
        return str(Path(*parts[-2:])) if len(parts) > 2 else address

    def source_for(self, plan: AlbumPlan | None = None):
        """The provider this plan belongs to — the one the Service was built with when they agree.

        A Service holds one provider because a run is usually about one album; a plan from another
        provider gets its own. Tests inject a stand-in as `yt`, and it stands in for the default.
        """
        if plan is None:
            return self.yt      # a fetch has no plan yet: it is this Service's own provider
        wanted = getattr(plan, "provider", None) or sources.DEFAULT
        if wanted == getattr(self.yt, "name", sources.DEFAULT):
            return self.yt
        return sources.get(wanted, self.cfg, self._cancel)

    def _track_source(self, plan: AlbumPlan):
        """Which provider each track's audio comes from — its chosen candidate's, not the album's."""
        def whose(track: PlanTrack):
            wanted = getattr(sources.for_candidate(track, plan, self.cfg, self._cancel), "name", None)
            if wanted == getattr(self.yt, "name", sources.DEFAULT):
                return self.yt          # a Service built with a stand-in keeps using it
            return sources.get(wanted, self.cfg, self._cancel)

        return whose

    def _audio_may_be_sent(self, plan: AlbumPlan, capability: str, what: str) -> None:
        """Refuse to hand a private album's audio to a provider that is not this machine.

        **The audio, not a title** (§9, slice 75, R-258 defect 2). A lookup sends a name and a length;
        a timing provider is sent *the recording*, and for an album somebody paid a creator for that
        is the one thing that must not happen by accident. Allowed when the provider runs here —
        `local`, or `http` on a loopback endpoint — or when the album's owner turned lookups on for
        that album, which is the same switch that lets it be looked up at all.
        """
        if may_send_audio(self.cfg, plan, capability):
            return
        whose = timing_kind(self.cfg, capability)
        if whose == "none":
            # **asked in the right order** (§9, slice 77): with nothing configured there is nobody to
            # send a recording to, and saying "`none` would have to send it off this machine" names a
            # provider that does not exist. `_timing` refuses next, with the sentence that fits.
            return
        raise TimingUnavailable(
            f"this album's audio came from a source one person paid its creator for, and `{whose}` "
            f"would have to send the recording off this machine to {what} — use a provider that runs "
            "here (`local`, or `http` on this machine), or set \"lookups\": true in this album's plan")

    def _timing(self, capability: str, what: str):
        """The provider configured for *this* capability, if it can do the thing being asked (§9, slice 40).

        Held, not built: the same provider comes back for the next track, with its models still on the
        card (§9, slice 82). Reloading them was measured at about two seconds a track off a warm disk.
        """
        engine = self.engines.provider(self.cfg, capability)
        if capability not in engine.capabilities():
            raise TimingUnavailable(f"the {engine.name} timing provider cannot {what}")
        if hasattr(engine, "log"):
            engine.log = self.log
        return engine

    @contextmanager
    def _what_it_listens_to(self, audio: Path):
        """The recording as it is for a listener on this machine; the isolated voice for a vendor.

        **Both halves measured** (§9, slice 83, `docs/qa-catalog.md` BV). The local model does better
        on the track as it stands — 448 lines placed against 399 over fifteen tracks — and on the
        reported case the separated stem lost it almost entirely (193 words heard against 36). A
        speech service over a band writes down *nothing*: five of six tracks came back from Deepgram
        with an empty transcript, the sixth with 129 words. So a vendor is sent the voice alone, which
        is also less of the record to send (§9, slice 45).
        """
        if stays_here(self.cfg, LISTEN):
            yield audio
            return
        # imported here and nowhere else, so an installation with no timing extra never touches it
        from .timing_local import separated_voice

        with tempfile.TemporaryDirectory(prefix="noaap-voice-") as tmp:
            voice = separated_voice(audio, Path(tmp) / "voice.wav", self.log, self.check)
            if voice:
                self.log("sending the isolated voice, not the recording: a speech service hears "
                         "nothing through a band")
            yield voice or audio

    def _cover_of(self, plan: AlbumPlan, album_dir: Path) -> bytes | None:
        """The cover bytes a tidying pass would embed: whatever is on disk, never fetched."""
        from .download import _cover

        try:
            return _cover(plan, album_dir, self.source_for(plan), fetch=False)
        except Exception:  # a report may not fail over a cover
            log.debug("could not read the cover of %s", album_dir, exc_info=True)
            return None

    def _where_it_runs(self, engine: Any, capability: str) -> dict[str, str]:
        """Ask a provider that runs here where it will run, before the job says anything else.

        A provider somewhere else has no answer to this and is not asked: `{}` then, and the call
        below is the one it always was (§9, slice 82).
        """
        decide = getattr(engine, "device_now", None)
        return {"device": decide(capability)} if decide else {}

    def sync_lyrics(self, source_id: str) -> Outcome:
        """Make the plan agree with the `.lrc` files beside the tracks, and the tags with the plan.

        The same `reconcile` the passes run (§9, slice 21), called for its own sake: the web UI asks for
        this when an album is opened and the two had drifted apart — a sidecar edited or deleted
        outside noaap. Nothing is looked up and nothing is downloaded.
        """
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        changed = [t for t in plan.tracks
                   if t.state == "done" and reconcile(album_dir, t, album_dir / t.filename)[1]]
        if not changed:
            self.log("the lyrics beside these tracks are what the plan says they are")
            return Outcome("ok", plan, album_dir)
        for t in changed:
            mine = " — yours from now on" if t.provenance.get("lyrics") == Provenance.USER else ""
            self.log(f"{t.title}: {t.lyrics}{mine}")
        save_plan(plan, album_dir)
        self._run(plan, album_dir, download=False)
        save_plan(plan, album_dir)
        return Outcome("ok", plan, album_dir)

    def check_near_lyrics(self, source_id: str, video_id: str) -> Outcome:
        """An lrclib entry that is nearly this recording: is it this song's, and is its clock ours?

        `get` takes a candidate within three seconds and nothing else, which is right as far as the
        length can tell (§9, slice 27). This asks the only question the length cannot answer, by aligning
        the candidate's words to the file: how many of them the aligner can place says whether these
        are the song's words, and how much of the singing they span says whether the entry's timings
        belong to *this* cut (§9, slice 46). Measured over this library's 203 near-misses: 143 are taken
        whole, 16 are the song's words on another cut, one is a different song.
        """
        api = self.lrclib
        if api is None:
            return Outcome("failed", message="lyrics are switched off — turn them on with: noaap config --lyrics on")
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        if not self.may_look_up(plan):
            return Outcome("failed", message=("nothing about this album is asked of lrclib: its audio "
                                              "came from a source one person paid its creator for"))
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return Outcome("failed", message="no such track in this album")
        if track.state != "done" or not track.file_length:
            return Outcome("failed", message=f"{track.title}: there is no finished file to check against")
        if (track.lyrics or "none") != "none":
            return Outcome("failed", message=f"{track.title} already has words")

        entry, apart = self._nearest_entry(api, track)
        if not entry:
            track.lyrics_no_entry = datetime.now(UTC).date().isoformat()
            save_plan(plan, album_dir)
            self.log(f"{track.title}: lrclib has nothing else for this title")
            return Outcome("ok", plan, album_dir)
        track.lyrics_no_entry = None   # there is one after all; the next pass should look again
        theirs = float(entry.length or 0.0)
        if not nominated(track.file_length, theirs):
            track.lyrics_fit = {"entry": str(entry.lrclib_id or ""), "ours": f"{track.file_length:.1f}",
                                "theirs": f"{theirs:.1f}", "decided": "shown", "why": fit_reason(track.file_length, theirs)}
            save_plan(plan, album_dir)
            self.log(f"{track.title}: lrclib's entry is {apart:.0f} s away — too far to be worth an alignment; "
                     f"the panel says what it looks like ({track.lyrics_fit['why']})")
            return Outcome("ok", plan, album_dir)

        words = plain_lines(entry.text or "")
        if not words:
            return Outcome("ok", plan, album_dir)
        engine = self._timing(ALIGN, "align words")
        where = self._where_it_runs(engine, ALIGN)
        self.log(f"{track.title}: lrclib's entry is {apart:.0f} s from this file — asking the aligner "
                 f"whether its {len(words)} lines belong to it")
        timed = engine.align(album_dir / track.filename, words, check=self.check, **where)
        span = self._fit_number(timed.parameters.get("own_span"))
        unplaced = len(timed.unplaced) / max(1, len(words))
        say = fit_verdict(span, unplaced)
        track.lyrics_fit = {"entry": str(entry.lrclib_id or ""), "ours": f"{track.file_length:.1f}",
                            "theirs": f"{theirs:.1f}", "span": "" if span is None else f"{span:.3f}",
                            "unplaced": f"{unplaced:.3f}", "decided": say}
        if say == "reject":
            if entry.lrclib_id and entry.lrclib_id not in track.lyrics_rejected:
                track.lyrics_rejected.append(entry.lrclib_id)
            self.log(f"{track.title}: not this song — the aligner could not place {unplaced:.0%} of those words")
        elif say == "unclear":
            self.log(f"{track.title}: the aligner cannot tell (it placed {1 - unplaced:.0%} of the lines "
                     f"across {span:.0%} of the singing); the panel shows the numbers")
        elif say == "words+stamps":
            write_sidecar(album_dir, track, (entry.text or "").strip())
            track.lyrics, track.lyrics_id = status_of(entry.text or ""), entry.lrclib_id
            track.lyrics_length = theirs
            track.provenance.pop("lyrics", None)   # lrclib's words are lrclib's (§9, slice 21)
            track.lyrics_timed_by = None
            self.log(f"{track.title}: lrclib's words and timings fit this file ({span:.0%} of the singing)")
        else:  # the song's words on another cut: keep the words, use our own clock
            ours = "\n".join(stamped(line.text, line.start) for line in timed.lines)
            write_sidecar(album_dir, track, ours)
            track.lyrics, track.lyrics_id = "synced", entry.lrclib_id
            track.lyrics_length = theirs
            track.provenance.pop("lyrics", None)   # their words, our clock
            track.lyrics_timed_by = timed.by
            track.lyrics_fit["why"] = fit_reason(track.file_length, theirs)
            self.log(f"{track.title}: lrclib's words are this song's, its timings are {track.lyrics_fit['why']}'s — "
                     f"kept the words and timed them to this file with {timed.by}")
        save_plan(plan, album_dir)
        self._run(plan, album_dir, download=False)
        save_plan(plan, album_dir)
        return Outcome("ok", plan, album_dir)

    # a fit that decided nothing is worth asking again when the user asks for a re-check; one that
    # took the words is not, and a rejected entry is excluded by `_nearest_entry` whatever we do here
    RECHECKABLE = ("shown", "unclear", "reject")

    def check_near_lyrics_all(self, refetch: bool = False, artist: str | None = None,
                              dry_run: bool = False) -> list[Outcome]:
        """Every track with no words whose lrclib entry was refused for its length (§9, slice 46).

        The per-track check exists behind a button; this is the pass. Nothing new is decided here —
        each track goes through `check_near_lyrics`, so the verdict is the same one the panel gives.

        `dry_run` does the lookups and none of the alignments: it says which tracks have a candidate,
        how far it is, and how many alignments the real pass would spend. That matters because an
        alignment is about 11 s on a GPU and 166 s on a processor, so the count is the price.
        """
        api = self.lrclib
        if api is None:
            self.log("lyrics are switched off — turn them on with: noaap config --lyrics on")
            return []
        if not dry_run and ALIGN not in capabilities_of(self.cfg):
            message = ("checking a near miss needs a timing provider that can align — "
                       "see `timing_align_provider` in the config")
            self.log(message)
            return [Outcome("failed", message=message)]

        albums = list(iter_plans(self.library)) if self.library and self.library.exists() else []
        if artist:
            albums = [(d, p) for d, p in albums if p.albumartist.casefold() == artist.casefold()]

        wanted: list[tuple[AlbumPlan, PlanTrack]] = []
        for _, plan in albums:
            if not self.may_look_up(plan):
                continue
            for track in plan.tracks:
                if track.state != "done" or (track.lyrics or "none") != "none" or not track.file_length:
                    continue
                decided = (track.lyrics_fit or {}).get("decided")
                settled = decided is not None or track.lyrics_no_entry is not None
                if not settled or (refetch and (decided in self.RECHECKABLE or track.lyrics_no_entry)):
                    wanted.append((plan, track))
        if not wanted:
            self.log("near misses: nothing to check")
            return []

        self.log(f"near misses: {len(wanted)} track(s) with no words to look at"
                 + (" — dry run, nothing is aligned and nothing is written" if dry_run else ""))
        if dry_run:
            return [self._near_dry_run(api, wanted)]

        outcomes, counts = [], Counter()
        try:
            for i, (plan, track) in enumerate(wanted, 1):
                self.check()
                self.log(f"=== [{i}/{len(wanted)}] {track.artist} — {track.title}")
                outcome = self._guarded(lambda: self.check_near_lyrics(plan.source_id, track.video_id))
                outcomes.append(outcome)
                # the verdict is on the plan `check_near_lyrics` loaded, not on our copy of it
                after = next((t for t in (outcome.plan.tracks if outcome.plan else [])
                              if t.video_id == track.video_id), None)
                counts[(after.lyrics_fit or {}).get("decided") if after and after.lyrics_fit
                       else "no candidate"] += 1
        finally:
            # the card goes back whether the pass finished, was cancelled or failed (§9, slice 41).
            # Where the app is holding the provider, its idle window decides that instead — a pass is
            # not the last thing that will be asked of it (§9, slice 82).
            if self._owns_engines:
                self.engines.let_go()
            else:
                release_gpu_memory()
        self.log("near misses: " + (", ".join(f"{n} {what}" for what, n in counts.most_common())
                                    or "nothing decided"))
        return outcomes

    def _near_dry_run(self, api: LyricsAPI, wanted: list[tuple[AlbumPlan, PlanTrack]]) -> Outcome:
        """Lookups only: what is there, how far away, and how many alignments it would cost."""
        counts = Counter()
        for plan, track in wanted:
            self.check()
            entry, apart = self._nearest_entry(api, track)
            if not entry:
                counts["no candidate"] += 1   # a real pass would remember this and stop asking
                self.log(f"{track.artist} — {track.title}: lrclib has nothing else for this title")
                continue
            theirs = float(entry.length or 0.0)
            if nominated(track.file_length or 0.0, theirs):
                counts["would align"] += 1
                self.log(f"{track.artist} — {track.title}: entry {entry.lrclib_id} is {apart:.0f} s away "
                         f"({track.file_length:.0f} s vs {theirs:.0f} s) — would align")
            else:
                counts["too far to align"] += 1
                self.log(f"{track.artist} — {track.title}: entry {entry.lrclib_id} is {apart:.0f} s away "
                         f"({track.file_length:.0f} s vs {theirs:.0f} s) — {fit_reason(track.file_length or 0.0, theirs)}, "
                         "no alignment")
        self.log("near misses (dry run): "
                 + ", ".join(f"{n} {what}" for what, n in counts.most_common()))
        self.log(f"near misses (dry run): {counts['would align']} alignment(s) would run")
        return Outcome("ok", message=f"{counts['would align']} alignment(s) would run")

    @staticmethod
    def _fit_number(text: str | None) -> float | None:
        try:
            return float(text) if text else None
        except ValueError:
            return None

    def take_plain_lyrics(self, source_id: str, video_id: str, entry_id: int) -> Outcome:
        """Put an entry's words beside a track **without** its timings, on the user's own decision.

        For the cases nothing could decide (§9, slice 46): the words are probably this song's, the
        timestamps are for a recording of another length, and a person has looked at the two numbers
        and said take them. They stay lrclib's words — the ownership contract does not change
        because a human pressed the button (§9, slice 21).
        """
        api = self.lrclib
        if api is None:
            return Outcome("failed", message="lyrics are switched off")
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track or track.state != "done":
            return Outcome("failed", message="no such finished track in this album")
        entry = api.by_id(int(entry_id))
        if not entry or not entry.text:
            return Outcome("failed", message=f"lrclib entry {entry_id} has no words")
        words = "\n".join(plain_lines(entry.text))
        if not words.strip():
            return Outcome("failed", message=f"lrclib entry {entry_id} has no words")
        write_sidecar(album_dir, track, words)
        track.lyrics, track.lyrics_id, track.lyrics_length = "plain", entry.lrclib_id, entry.length
        track.provenance.pop("lyrics", None)   # lrclib's words, taken by hand but still theirs
        track.lyrics_timed_by = None
        track.lyrics_fit = {**(track.lyrics_fit or {}), "decided": "words by hand"}
        save_plan(plan, album_dir)
        self._run(plan, album_dir, download=False)
        save_plan(plan, album_dir)
        self.log(f"{track.title}: took lrclib's words without their timings, on your say-so")
        return Outcome("ok", plan, album_dir)

    def _nearest_entry(self, api: LyricsAPI, track: PlanTrack):
        """The candidate with words whose length is closest to this file's, and how far that is."""
        best, apart = None, None
        for row in api.candidates(track.artist, track.title) if hasattr(api, "candidates") else []:
            if row.lrclib_id in track.lyrics_rejected or not row.text or not row.length:
                continue
            gap = abs(float(row.length) - (track.file_length or 0.0))
            if apart is None or gap < apart:
                best, apart = row, gap
        return best, (apart or 0.0)

    def lookup_track(self, source_id: str, video_id: str, reject: bool = False) -> Outcome:
        """Ask lrclib about one track — or reject what it gave and ask again (DESIGN.md §9, slice 27).

        `reject` remembers the entry on the track, so no later lookup can pick it again: not this
        one, not a `--refetch`, not a fresh pass. Rejecting is about *that entry* being the wrong
        recording, which stays true however often it is asked for.
        """
        api = self.lrclib
        if api is None:
            return Outcome("failed", message="lyrics are switched off — turn them on with: noaap config --lyrics on")
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return Outcome("failed", message="no such track in this album")
        if track.state != "done":
            return Outcome("failed", message=f"{track.title}: there is no file yet to match lyrics against")
        if track.provenance.get("lyrics") == Provenance.USER:
            # their words are not lrclib's to replace; deleting them in the editor is the way back
            return Outcome("failed", message=f"{track.title}: these lyrics are yours — delete them first")
        if reject:
            if not track.lyrics_id:
                return Outcome("failed", message=f"{track.title}: there is no lrclib match to reject")
            if track.lyrics_id not in track.lyrics_rejected:
                track.lyrics_rejected.append(track.lyrics_id)
            self.log(f"lrclib #{track.lyrics_id} is not “{track.title}” — it will not be offered again")
            remove_sidecar(album_dir, track.filename)
            track.lyrics_sha = None
        track.lyrics = None  # not looked up: update_track does the asking
        text = update_track(api, plan, track, album_dir, album_dir / track.filename)
        if track.lyrics is None:
            # `update_track` swallows a LyricsError and leaves the status unset so the next pass
            # asks again. Saying "nothing fits" here would report a site that did not answer as
            # an answer — the one thing this log line must not do.
            self.log(f"{track.title}: lrclib could not be reached — asked again on the next pass")
        elif text:
            self.log(f"{track.title}: {track.lyrics} lyrics" + (f" (lrclib #{track.lyrics_id})" if track.lyrics_id else ""))
        else:
            self.log(f"{track.title}: nothing lrclib has fits this recording")
        save_plan(plan, album_dir)
        self._run(plan, album_dir, download=False)  # the tag follows the file
        save_plan(plan, album_dir)
        return Outcome("ok", plan, album_dir)

    def _lyrics_pass(self, plan: AlbumPlan, album_dir: Path, api: LyricsAPI) -> Outcome:
        self._run(plan, album_dir, download=False, lyrics=api)
        return Outcome("ok", plan, album_dir)

    # -- offline repair --------------------------------------------------------------------

    def find_again(self, plan: AlbumPlan, album_dir: Path, dry_run: bool = False) -> int:
        """Tracks whose file is not where the plan says, found where the collection says it is.

        **A library that 1.5.0 adopted needs this before anything else touches it** (§9, slice 61).
        Its plans hold a bare name for a track whose file is in a disc sub-folder, so the file is not
        at `album_dir / filename` — and the next `update` reads that as a missing file and copies it
        into the album root under a name of noaap's own. Fixing the writer does not fix those plans;
        this does, and `noaap repair` is where a library is brought up to date.

        Only an album that **is its own source** is asked, and only the provider says where a file is.
        A track the collection no longer offers is left exactly as it is: a plan that names a file
        nobody can find is a broken library and slice 60 reports it as one.
        """
        if not plan.keep_names or not lost_files(album_dir, plan):
            return 0
        if Path(plan.source_id).resolve() != album_dir.resolve():
            return 0  # it came from somewhere else, and `update` is what re-checks that one
        try:
            collection = sources.for_plan(plan, self.cfg, self.cancel).collection(str(album_dir))
        except sources.SourceError as e:
            self.log(f"cannot read the folder to look for the files: {e}")
            return 0
        where = {entry.video_id: entry.where for entry in collection.entries}
        found = 0
        for track in plan.tracks:
            if track.state != "done" or not track.filename or (album_dir / track.filename).is_file():
                continue
            if not (there := where.get(track.video_id)) or not (album_dir / there).is_file():
                continue
            if track.adopted_name == track.filename:
                track.adopted_name = there  # the record held the same wrong value, and it is a record
            track.filename = there
            found += 1
        if found:
            self.log(f"{found} track(s) {'would be' if dry_run else 'were'} found where the "
                     f"collection says they are")
        return found

    def take_out_strays(self, plan: AlbumPlan, album_dir: Path, apply: bool = False) -> dict[str, int]:
        """What 1.4.0 and 1.5.0 copied into an adopted album's root, moved to the bin (§9, slice 63).

        **Dry unless asked** (R-220): without `apply` it says what it would do and writes nothing, and
        that is the default for the whole pass, because this one removes a file from somebody's music
        folder. Nothing is deleted even then — the bin holds it, with the evidence for taking it.
        """
        from . import strays as strays_mod

        if not plan.keep_names or Path(plan.source_id).resolve() != album_dir.resolve():
            return {}
        source = sources.for_plan(plan, self.cfg, self.cancel)
        read = lambda folder: source.collection(str(folder))  # noqa: E731 — the provider, as a reader
        try:
            found, left = strays_mod.look(album_dir, plan, read)
        except sources.SourceError as e:
            self.log(f"cannot read the folder to look for strays: {e}")
            return {}
        held = strays_mod.held_by(plan, strays_mod.refs_of_discs(album_dir, read))
        for one in left:
            self.log(f"  not provably ours: {one.where} — {one.why}")
        if not found:
            return {"left": len(left)}

        library = self.library or album_dir
        done = {"strays": len(found), "left": len(left), "binned": 0, "tracks": 0}
        for one in found:
            self.log(f"  {'stray' if apply else 'would bin'}: {one.where} — a copy of {one.copy_of}")
            gone = strays_mod.only_a_copy(plan, one, held)
            if not apply:
                done["tracks"] += len(gone)
                continue
            audio = _inside(album_dir, one.where)
            speaks_for = gone[0] if gone else held.get(one.copy_of)
            if audio and speaks_for is not None and bin_track(
                    library, album_dir, plan, speaks_for,
                    reason=f"a copy noaap wrote into this album's root; the album's own file is "
                           f"“{one.copy_of}”",
                    audio=audio, sidecar=sidecar_path(album_dir, one.where),
                    evidence=one.evidence()):
                done["binned"] += 1
            # **the tracks that existed only because of the copy go with it.** The owner's own track
            # stays and is pointed back at its file by `find_again`, in this same pass.
            plan.tracks = [t for t in plan.tracks if t not in gone]
            done["tracks"] += len(gone)
        return done

    def find_moved(self, plan: AlbumPlan, album_dir: Path, apply: bool = False) -> dict[str, int]:
        """Tracks whose file was renamed or moved, found again by what the file holds (§9, slice 66).

        `find_again` asks the provider *where a ref's file is*, which answers a plan that recorded a name
        without its folder. This answers the cases where the name itself is gone: a file the owner
        renamed, or moved into a disc folder, or into another album. The identity decides, and where it
        does not decide, the track is named and left.

        **Nothing on disk is touched** (R-227, ruling 2): what changes is what the plan says.
        """
        from . import moved as moved_mod

        lost = [t for t in plan.tracks
                if t.state == "done" and t.filename and not (album_dir / t.filename).is_file()]
        if not lost:
            return {}
        library = self.library
        # the wider look is a *callable* so that it costs nothing unless the album itself has no answer:
        # a rename inside an album is the commonest case by far, and it never reads another folder
        wider = (lambda: moved_mod.audio_under(library)) if library and library.is_dir() else None
        out = moved_mod.look(album_dir, plan, library, lost=lost, also=wider)
        for one in out.found:
            self.log(f"  {'found' if apply else 'would find'} “{one.title}”: {one.was} → {one.now}")
        for one in out.left:
            self.log(f"  left “{one.title}” ({one.was}): {one.why}")
        # **`moved` is what was done, not what could be.** Counting a dry run's findings here made
        # `repair` save the plan it had only looked at, which is the one thing a dry run may not do.
        done = {"moved": 0, "would_move": len(out.found), "left": len(out.left), "decoded": out.decoded}
        if apply:
            done["moved"], measured = moved_mod.re_attach(plan, album_dir, out.found)
            done["would_move"] = 0
            done["decoded"] += measured
        return done

    def find_source(self, plan: AlbumPlan, album_dir: Path, root: Path,
                    apply: bool = False) -> dict[str, int]:
        """The folder this album was taken in from, found again under `root` (§9, slice 66).

        Only for an album whose source is not there any anymore, and only through the provider: it reads
        the folder the files were found in and mints the refs itself. The album's own files are not
        touched — what changes is which source the plan names.
        """
        from . import moved as moved_mod

        where = moved_mod.source_of(plan)
        if not where or Path(where).is_dir() or Path(where).resolve() == album_dir.resolve():
            return {}
        source = sources.for_plan(plan, self.cfg, self.cancel)
        try:
            found, why = moved_mod.look_for_source(album_dir, plan, root,
                                                   lambda folder: source.collection(str(folder)))
        except sources.SourceError as e:
            self.log(f"  cannot read that folder: {e}")
            return {}
        if found is None:
            self.log(f"  left “{plan.album}”: {why}")
            return {"sources": 0, "would_source": 0, "left": 1, "decoded": 0}
        self.log(f"  {'source' if apply else 'would point'} of “{plan.album}”: {found.was} → {found.now}")
        done = {"sources": 0, "would_source": 1, "left": 0, "decoded": found.decoded}
        if apply:
            moved_mod.re_source(plan, found, found.now)
            done["sources"], done["would_source"] = 1, 0
        return done

    def repair(self, dry_run: bool = False, strays: bool = False, apply: bool = False,
               find_moved: bool = False, under: Path | None = None) -> list[Outcome]:
        """Tidy the library without asking YouTube: performer-only artists, one spelling, lengths.

        Fixes albums downloaded before those rules existed — renames and retags only. With
        `dry_run` nothing at all is written: it says what it would do and stops there.

        **A dry finding is a dry pass** (R-234). `--strays` and `--find-moved` are dry until `--apply`,
        and it used to be only *their own* half that held back: the rest of `repair` ran and saved every
        plan it tidied, so somebody who asked what would happen got a library that had been written to —
        measured, as a hash over all 132 plans changing during a dry run. Without `--apply` the whole run
        writes nothing.
        """
        if (strays or find_moved) and not apply:
            dry_run = True
        outcomes = []
        # **an album this program cannot see is said out loud** (R-419, point 3), never skipped
        if line := says_the_old_name(self.library):
            self.log(line)
        lengths = retags = renames = pointed = cased = 0
        moved_total = {"moved": 0, "would_move": 0, "left": 0, "decoded": 0}
        decided = self._decide_spellings()  # every artist key settled before the first rename
        for album_dir, plan in list(iter_plans(self.library)) if self.library and self.library.exists() else []:
            before = (plan.albumartist, [(t.artist, t.title) for t in plan.tracks], len(plan.tracks))
            # **first, before anything here tidies a name** (§9, slice 63): a file in the album's root
            # was written under the name the plan held *then*, and a pass that recognises one has to
            # ask the plan as it is on disk. Taking the copies out first is also what makes the owner's
            # own files the only answer `find_again` can give below.
            swept = self.take_out_strays(plan, album_dir, apply=apply and not dry_run) if strays else {}
            if swept.get("strays"):
                acted = apply and not dry_run
                self.log(f"{swept['strays']} stray file(s) {'moved to the bin' if acted else 'would be moved to the bin'}"
                         f", {swept['tracks']} track(s) that held only a copy "
                         f"{'removed' if acted else 'would be removed'}")
            seen: set[str] = set()  # the same video listed twice in a playlist is one track
            unique = [t for t in plan.tracks if not (t.video_id in seen or seen.add(t.video_id))]
            if len(unique) != len(plan.tracks):
                self.log(f"{len(plan.tracks) - len(unique)} duplicate track(s) removed from the album")
                plan.tracks = unique
                # **and the gap stays** (R-373): a track number changes only by the user's own
                # reorder. Closing it here renumbered everything after the drop, which on an
                # adopted rip writes a wrong position into the files; the gap documents the drop.
            # a length belongs to the recording it was read from; tracks whose recording was
            # refused ("(Live)", a cover) kept one anyway and read as minutes off (fixed 2026-09-25)
            borrowed = sum(bool(t.mb_length and not t.mbid) for t in plan.tracks)
            if borrowed:
                for t in plan.tracks:
                    if t.mb_length and not t.mbid:
                        t.mb_length = None
                self.log(f"{borrowed} track(s) gave up a length taken from another recording")
            # **an adopted album's values are its owner's** (R-410, ruling 5), so none of the
            # tidying below touches one unless the library asks for it. It used to run over every
            # album there is, which would have put back at the next repair exactly what a take-in
            # had just been taught to leave alone.
            may_tidy = not plan.adopted or bool(getattr(self.cfg, "tidy_adopted_tags", False))
            for t in plan.tracks if may_tidy else []:
                if t.provenance.get("artist") == Provenance.SOURCE_TAGS and ", " in t.artist:
                    t.artist = t.auto["artist"] = t.artist.split(", ")[0]  # writers and producers
                if Provenance.USER in (t.provenance.get("artist"), t.provenance.get("title")):
                    continue  # the user decided how this one reads
                artist, title = move_feat(t.artist, t.title)  # guests belong in the title
                title = strip_self_feat(artist, title)
                if (artist, title) != (t.artist, t.title):
                    t.artist, t.title = artist, title
                    t.auto.update(artist=artist, title=title)
            editable = [t for t in plan.tracks if t.provenance.get("title") != Provenance.USER]
            if may_tidy and (dropped := drop_album_name(plan.album, editable)):
                self.log(f"{dropped} track title(s) lost the repeated album name")
            if may_tidy and plan.kind != Kind.COMPILATION and plan.provenance.get("albumartist") in (Provenance.SOURCE_TAGS, Provenance.SOURCE_TITLE):
                names = [t.artist for t in plan.tracks]
                if names:
                    plan.albumartist = plan.auto["albumartist"] = max(set(names), key=names.count)
            # **one spelling per artist, whatever the library asks of adopted albums** (§9, slice
            # 126): a case-only difference is two folders on a case-sensitive share, and nothing of
            # what the tag says changes. The rest of the spelling work stays behind `tidy_adopted_tags`.
            if said := self.one_spelling_per_artist(plan, decided):
                self.log(f"  the artist is spelled {said} elsewhere in the library — using that")
                cased += 1
            if may_tidy:
                self._adopt_track_spelling(plan)  # an album that disagrees with its own tracks
                self._apply_spelling(plan, decided)
                if named := set_single_album_name(plan):
                    self.log(f"the single is named after its track: “{named}”")
            # a plan can be right while the folder is not: the album artist was unified
            # earlier without moving anything (fixed 2026-09-24, but the folders remain)
            # **a whole library is converted here** (§9, slice 60, R-207 ruling 2): a plan whose
            # written form differs from the file — a path inside the album that is still absolute —
            # is saved even when nothing else about the album needs tidying.
            stale = rewritten(album_dir, plan)
            refound = self.find_again(plan, album_dir, dry_run=dry_run)
            # after `find_again`, which is cheap and asks the provider: only what is still missing is
            # worth decoding for (§9, slice 66)
            elsewhere = self.find_moved(plan, album_dir, apply=apply and not dry_run) if find_moved else {}
            if find_moved and under is not None:
                for key, value in self.find_source(plan, album_dir, under,
                                                   apply=apply and not dry_run).items():
                    if key in ("sources", "would_source"):
                        moved_total[key] = moved_total.get(key, 0) + value
                    elsewhere[key] = elsewhere.get(key, 0) + value
            if elsewhere.get("moved") or elsewhere.get("would_move") or elsewhere.get("left"):
                acted = elsewhere.get("moved", 0)
                self.log(f"{acted or elsewhere.get('would_move', 0)} track(s) "
                         f"{'found again' if acted else 'would be found again'}, "
                         f"{elsewhere.get('left', 0)} left, {elsewhere.get('decoded', 0)} file(s) decoded")
            for key in ("moved", "would_move", "left", "decoded"):
                moved_total[key] += elsewhere.get(key, 0)
            misplaced = album_dir != self.library / wanted_folder(plan)
            # **before the skip, not after it.** An album whose names are already right used to be
            # dropped here, and with it the only pass that would have measured its files — which is
            # why a tidy library kept hundreds of tracks with no length at all (§9, slice 56).
            filled = self.measure_lengths(plan, album_dir, dry_run=dry_run)
            lengths += filled
            # **asked before the skip** (§9, slice 86): the plan of an album whose files were cut with a
            # clock starting before zero is perfectly tidy, so without this nothing would ever look at
            # those files again — and they are the ones a player begins in their own middle.
            recut = needs_a_recut(plan, album_dir)
            # **asked before the skip** (§9, slice 123): an album whose numbers repeat is otherwise
            # perfectly tidy, and the lookup that can settle its discs was the take-in's alone.
            # Only an *assignment* keeps the album here: a partial match changes nothing, so an album
            # that is otherwise tidy is still tidy and is skipped like any other (§9, slice 124).
            discs = bool(self._look_at_the_discs(plan, album_dir, dry_run))
            want_here = for_album(self.cfg, plan)
            # **a plan that says its album is somewhere it is not is re-pointed** (§9, slice 125),
            # before the skip: a cover address under a staging folder that no longer exists cannot be
            # read, and an album it keeps out of the skip is an album nothing ever finishes with.
            repointed = point_at(plan, album_dir)
            if repointed:
                self.log(f"  {'would be' if dry_run else ''} re-pointed at its own folder "
                         f"(its plan named {album_dir.name!r} elsewhere)")
                pointed += 1
                if not dry_run:
                    save_plan(plan, album_dir)
            # **and the one question that cannot drift from the apply: what would the apply do?**
            # (§9, slice 124). Eleven conditions guessed at this and none of them asked whether a
            # *file* would be renamed or retagged — so slice 116's renames reached only the albums
            # that failed the skip for some other reason. `Mono Inc. — Temple Of The Torn` was
            # renamed because it still had ID3v1 tails; `Mono Inc. — Head Under Water` had none and
            # was skipped, and so were `Nightwish — Human. :II: Nature.` and the four discs of
            # `Schandmaul — Sinnfonie` whose files said three. Found only because the disc lookup
            # above happened to un-skip them. `would_do` is what the dry run already prints and what
            # `run` then does, so asking it here is the same answer by construction.
            # It also subsumes the three questions that were asked here one by one — a tail to drop,
            # a comment, a Windows Media frame — because `would_do` prints a line for each.
            would = [line for line in
                     would_do(plan, album_dir, self._cover_of(plan, album_dir), self.library,
                              want_here)
                     if MAYBE_COVER not in line]   # that one promises nothing (§9, slice 124)
            if not misplaced and not borrowed and not filled and not stale and not refound \
                    and not swept.get("binned") and not elsewhere.get("moved") \
                    and not elsewhere.get("sources") and not recut and not discs and not would \
                    and not repointed \
                    and before == (plan.albumartist, [(t.artist, t.title) for t in plan.tracks], len(plan.tracks)):
                continue
            self.log(f"=== {plan.albumartist} — {plan.album}"
                     + (f" ({filled} track(s) measured)" if filled else ""))
            if dry_run:
                # **the dry run names what the real run would do to the files** (§9, slice 85). It used
                # to stop here, so renames and retags — which happen inside `run` below — were never
                # mentioned: the user was told no audio file would be touched and 376 were rewritten.
                want = want_here
                if stopped := held_back(self.cfg, plan):
                    self.log(f"  this album is excepted from: {', '.join(stopped)}")
                for line in would:
                    self.log(f"  {line}")
                retags += sum(1 for line in would if "retagged" in line or "rewritten" in line)
                renames += sum(1 for line in would if "renamed" in line)
                outcomes.append(Outcome("ok", plan, album_dir))
                continue
            save_plan(plan, album_dir)
            want = for_album(self.cfg, plan)
            album_dir = relocate(album_dir, plan, self.library, want)
            self._run(plan, album_dir, download=False)
            # **a move, a rename and the re-point of what they touched are one pass** (§9, slice 130).
            # The question above is asked before the skip, which is before the folder moves and before
            # `_run` renames the files — so on the user's share `repair --apply` moved 15 folders and
            # left 13 plans naming the folder they had just left, a second apply fixed 13 and left the
            # one it had moved itself, and a third was needed for that. Asked again here, where the
            # album is where it is going to stay, it is the same pass that moved it.
            if point_at(plan, album_dir):
                if not repointed:
                    self.log("  re-pointed at its own folder, which this pass has just moved it to")
                    pointed += 1
                save_plan(plan, album_dir)
            outcomes.append(Outcome("ok", plan, album_dir))
        if moved_total.get("sources") or moved_total.get("would_source"):
            self.log(f"{moved_total.get('sources') or moved_total.get('would_source')} album(s) "
                     f"{'were' if moved_total.get('sources') else 'would be'} pointed at the folder "
                     "their source moved to")
        if moved_total["moved"] or moved_total["would_move"] or moved_total["left"]:
            self.log(f"{moved_total['moved'] or moved_total['would_move']} track(s) "
                     f"{'were' if moved_total['moved'] else 'would be'} found again by what their file "
                     f"holds, {moved_total['left']} left as they are; "
                     f"{moved_total['decoded']} file(s) decoded")
        if lengths:
            self.log(f"{lengths} track(s) {'would get' if dry_run else 'got'} the length of their file")
        if dry_run and (retags or renames):
            # said as its own total, because "246 albums would be tidied up" is not an answer to
            # "will this write into my audio files?" (§9, slice 85)
            self.log(f"{renames} file(s) would be renamed and {retags} audio file(s) would be "
                     "rewritten (their tags)")
        if cased:
            self.log(f"{cased} album(s) {'would be' if dry_run else 'were'} given the library's "
                     "spelling of their artist")
        if pointed:
            log_it = "would be re-pointed" if dry_run else "re-pointed"
            self.log(f"{pointed} plan(s) {log_it} at their own album folder")
        self.log(f"{len(outcomes)} album(s) {'would be tidied up' if dry_run else 'tidied up'}")
        if self.library and self.library.is_dir():
            # **removing them is the setting; saying they are there is not** (§9, slice 104, 115).
            # Off, a pass clears only the folders it emptied itself and leaves the owner's alone —
            # but an empty folder a *rename* left behind is this program's own litter, and the user
            # found 91 of them on the share after the collection came in. So they are always named,
            # and `remove_empty_folders` decides whether they also go.
            sweep = bool(self.cfg.remove_empty_folders)
            empty = empty_under(self.library, everything=True)
            for folder in empty:
                if sweep:
                    self.log(f"  {'would remove' if dry_run else 'removed'} the empty folder "
                             f"{folder.relative_to(self.library)}")
                    if not dry_run:
                        with contextlib.suppress(OSError):
                            folder.rmdir()
            if empty and not sweep:
                self.log(f"{len(empty)} empty folder(s) are under the library and were left alone "
                         "(`remove_empty_folders` is off): "
                         + ", ".join(str(f.relative_to(self.library)) for f in empty[:3])
                         + (" …" if len(empty) > 3 else ""))
        return outcomes

    # -- taking a folder in (§9, slice 92) -----------------------------------------------

    def take_in(self, folder: Path, mode: str = "merge", dry_run: bool = True,
                new: bool = False) -> Outcome:
        """Take another folder into the library: the same two passes the command line runs.

        `merge` compares it with what is here and takes the better copies (the other side is never
        written to); `adopt` takes it in where it stands, one plan per album and nothing else, with
        that folder as its own root. **The
        lines are the command's own** — `report()` from either pass — so what the page shows is what
        `noaap merge`/`noaap adopt` print, and a check followed by an apply does what the check listed.
        """
        if self.library is None:
            return Outcome("failed", message="no library is configured")
        if why := refuse_folder(folder, self.library):
            return Outcome("failed", message=why)
        if mode not in ("merge", "adopt"):
            return Outcome("failed", message=f"unknown way of taking a folder in: {mode!r}")
        if mode == "merge":
            from . import merge as merge_pass

            found = merge_pass.survey(folder, self.library, log=self.log)
            for line in merge_pass.report(found, applying=not dry_run):
                self.log(line)
            if new:
                # **the albums this library does not have at all** (§9, slice 92, the `--new` half):
                # a merge compares what both sides hold, so an album only they have is invisible to it
                # until it is asked for by name.
                missing = merge_pass.unpaired_albums(found)
                self.log(f"{len(missing)} album(s) are not in this library at all")
                for album_dir, tracks in sorted(missing.items()):
                    self.log(f"  {tracks[0].plan.albumartist} — {tracks[0].plan.album} "
                             f"({len(tracks)} track(s))")
            if dry_run:
                return Outcome("ok", message="nothing was written")
            self.check()
            done = merge_pass.carry_out(found, self.library, log=self.log)
            if new:
                got = merge_pass.take_new(found, lambda url: self.fetch(url), log=self.log)
                self.log(f"{got['taken']} album(s) fetched, {got['held']} already here and left alone")
            self.log(f"{done['replaced']} replaced, {done['filled']} filled, "
                     f"{done['offered']} listed for you to decide"
                     + (f", {done['failed']} could not be taken" if done["failed"] else ""))
            return Outcome("ok", message=f"{done['replaced']} replaced, {done['filled']} filled")
        from . import adopt as adopt_pass

        # **adopt takes the folder in as its own root** (§9, slice 92): the plan a folder gets says
        # where its album is *relative to the library it belongs to*, and this folder is not inside
        # this library — a folder inside it is refused above. So the folder is its own root, which is
        # what `noaap adopt PATH` prints when PATH is what the library points at: one plan per album,
        # beside the audio, and the folder can then be pointed at, watched, or merged from.
        found = adopt_pass.survey(folder, folder, sources.get("folder", self.cfg), log=self.log)
        for line in adopt_pass.report(found, applying=not dry_run):
            self.log(line)
        if dry_run:
            return Outcome("ok", message="nothing was written")
        self.check()
        done = adopt_pass.carry_out(found, log=self.log)
        self.log(f"{done['adopted']} album(s) adopted, {done['tracks']} track(s)")
        return Outcome("ok", message=f"{done['adopted']} album(s) adopted")

    def take_in_all(self, folder: Path, *, names: str | None = None, keep: Path | None = None,
                    dry_run: bool = True, **switches: bool) -> Outcome:
        """`noaap take-in` from the page: a whole collection to one state (§9, slice 101)."""
        from . import intake

        if self.library is None:
            return Outcome("failed", message="no library is configured")
        if why := refuse_folder(folder, self.library, itself=True):
            return Outcome("failed", message=why)
        # without an answer the setting decides, as it does on the command line (R-410, ruling 3)
        chosen = names or ("scheme" if self.cfg.rename_adopted else "keep")
        choices = intake.Choices(names=chosen, names_from="you" if names else
                                 "the rename_adopted setting",
                                 **{k: bool(v) for k, v in switches.items()})
        done = intake.take_in(self, folder, choices, dry_run=dry_run, keep=keep, log=self.log)
        what = (f"{done.adopted} album(s), {done.tracks} track(s)"
                + (" would be taken in" if dry_run else " taken in"))
        return Outcome("ok", message=what)

    def _look_at_the_discs(self, plan: AlbumPlan, album_dir: Path, dry_run: bool) -> str:
        """Ask MusicBrainz which disc each file of this album is on, where its numbers repeat.

        **Asked by `repair` and `update` as well as by a take-in** (§9, slice 123, R-462). It used to
        be the take-in's alone — and a take-in skips an album that already holds a plan, so
        `Crematory/Early Years` (18 files, two runs of 1 to 9) could not be asked again by anything
        once it was in. One lookup, only for such an album, and the discs are the only thing it may
        change; `intake.ask_about_the_discs` is the one place that decides and clears the flag.
        """
        from . import intake

        where = str(album_dir.relative_to(self.library)) if self.library else album_dir.name
        if not intake.a_look_at(plan, where):
            return ""
        if not (self.cfg.musicbrainz and may_look_up(self.cfg, plan) and (mb := self.mb)):
            return ""
        look, said = intake.ask_about_the_discs(plan, where, mb)
        if look is None:
            self.log(f"  discs {'would be' if dry_run else ''} assigned from MusicBrainz — {said}")
            if not dry_run:
                save_plan(plan, album_dir)
            return said
        # **a partial match is reported and changes nothing** (§9, slice 124), so it returns nothing:
        # an album the lookup could not settle is as tidy as it was, and keeping it out of the skip
        # for that would have given 21 albums of the collection a full pass on every repair for ever.
        self.log(f"  the numbers repeat and MusicBrainz did not settle it: {said}")
        return ""

    def set_exception(self, source_id: str, key: str, on: bool) -> Outcome:
        """Except this album from one of the library's operations, or stop excepting it.

        **Only this writes an exception** (§9, slice 100). No pass ever sets one: an album that a pass
        could quietly except from the library's state would be a second state for the library to be in.
        """
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message="no such album")
        album_dir, plan = found
        plan.exceptions = with_exception(plan, key, on) or None
        save_plan(plan, album_dir)
        said = says_exceptions(plan)
        return Outcome("ok", plan, album_dir,
                       message=f"{plan.album}: {said}" if said else f"{plan.album}: no exceptions")

    # -- deleting (always asked for explicitly) -------------------------------------------

    def delete_track(self, source_id: str, video_id: str) -> Outcome:
        """Delete one track: its files go, and it leaves the album.

        Nothing is remembered — if the video is still in the source, the next fetch
        brings it back.
        """
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return Outcome("failed", message="no such track")
        entry = self._bin(album_dir, plan, track, DELETED, audio=_inside(album_dir, track.filename))
        self.log(f"moved {track.number:02d} {track.artist} - {track.title} to the recycle bin"
                 + (f" ({entry.name})" if entry else " — nothing was on disk"))
        plan.tracks.remove(track)
        # **the gap the deletion leaves stays** (R-373). For an adopted CD rip a closed gap writes a
        # wrong position into every file after it, which is R-372's defect by another route; for a
        # fetched album the gap is the truth of the deletion, and the editor's reorder is one drag
        # away. Only the user's own reorder moves a number now.
        save_plan(plan, album_dir)
        return self.execute(plan, album_dir)  # renames and retags the rest

    @staticmethod
    def _lowest_free(plan: AlbumPlan, track: PlanTrack) -> int:
        """The lowest number not already used on this track's disc, counting from 1."""
        taken = {t.number for t in plan.tracks if t is not track and t.disc == track.disc}
        number = 1
        while number in taken:
            number += 1
        return number

    def _bin(self, album_dir: Path, plan: AlbumPlan, track: PlanTrack, reason: str, *,
             audio: Path | None = None, ranking: dict[str, Any] | None = None) -> Path | None:
        """Move a track's files to the bin. Without a library root there is nowhere to put them,
        so the old behaviour stands — that only happens for a plan opened by path in a test."""
        # the sidecar is only touched when the audio name itself passed `_inside`: `sidecar_path`
        # joins the plan's filename without checking, and a tampered plan must not reach outside
        sidecar = sidecar_path(album_dir, track.filename) if audio else None
        if not self.library:
            for path in (audio, *kept_originals(album_dir, track)):
                if path and path.exists():
                    path.unlink()
            if sidecar:
                sidecar.unlink(missing_ok=True)
            return None
        return bin_track(self.library, album_dir, plan, track, reason,
                         audio=audio, sidecar=sidecar, ranking=ranking)

    def restore(self, entry_id: str) -> Outcome:
        """Put something binned back where it came from (DESIGN §9, slice 49).

        Three shapes, because a bin that can only undo the small decisions is not much of a bin:

        * **an album** — the folder, the plan and the cover come back, and every track entry of it
          still in the bin comes with them;
        * **a track whose album is gone** — the album shell is rebuilt from its album entry first,
          then the track. Only if that entry has been emptied too is there nothing to do;
        * **a track the plan still lists but whose file is missing** — a *repair*. An interrupted
          delete leaves exactly this (binning happens before the plan is saved, on purpose), and so
          does a crash or a Ctrl-C.

        Restoring is deliberately not symmetric with binning. **The user's lyrics win:** a sidecar
        written while the track was gone is kept and the restore says so. **Tags are rewritten** by
        the ordinary pass rather than replayed, so a track restored after its album was renamed gets
        today's names. And a track the source no longer lists **comes back as it was** for the next
        `prune` to move aside again — restore undoes one action, it does not argue with the playlist.
        """
        from .recycle import album_entry
        from .recycle import find as find_entry

        if not self.library:
            return Outcome("failed", message="no library configured")
        entry = find_entry(self.library, entry_id)
        if not entry:
            return Outcome("failed", message=f"no such recycle entry: {entry_id}")
        if entry.is_album:
            return self._restore_album(entry)
        if entry.is_file_entry:
            return self._restore_file(entry)
        if not entry.data.get("track"):
            return Outcome("failed", message=f"{entry.id} holds nothing that can be put back")

        found = self.find_album(entry.source_id)
        if not found:
            shell = album_entry(self.library, entry.source_id)
            if not shell:
                return Outcome("failed", message=(
                    f"{entry.describe()}: its album is not in the library and its album entry has "
                    "been emptied, so there is nothing to rebuild the folder from. Fetch the album "
                    "again and restore then."))
            self.log(f"{entry.data['album']} is gone; rebuilding it from the bin first")
            rebuilt = self._restore_album(shell, only=[entry.id])
            if rebuilt.status != "ok":
                return rebuilt
            found = self.find_album(entry.source_id)
            if not found:
                return Outcome("failed", message=f"could not rebuild {entry.data['album']}")
        return self._restore_track(entry, *found)

    def _restore_file(self, entry: Entry) -> Outcome:
        """Put back one file that is not a track's audio — a cover an undo set aside (§9, slice 65)."""
        assert self.library
        where = entry.data.get("where") or ""
        album_dir = self.library / str(entry.data.get("folder") or "")
        back = _inside(album_dir, where)
        held = entry.audio
        if back is None or held is None:
            return Outcome("failed", message=f"{entry.describe()}: nothing to put back")
        if back.exists():
            return Outcome("held", message=f"{where} is there again; the bin keeps its copy")
        back.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(held), back)
        self.log(f"{where} is back in {album_dir.name}")
        return Outcome("ok", message=f"{where} restored")

    def _restore_album(self, entry: Entry, only: list[str] | None = None) -> Outcome:
        """Rebuild a binned album: the folder, the plan, the cover, and its tracks still in the bin.

        `only` restores the shell and just those track entries — used when somebody asked for one
        track of an album that is gone, so they get that track and not fifty others.
        """
        from .recycle import find as find_entry

        assert self.library
        plan = AlbumPlan.from_dict(entry.data["plan"])
        album_dir = self.library / plan.folder
        existing = load_plan(album_dir)
        wanted = [i for i in entry.data.get("tracks", []) if only is None or i in only]

        skipped: list[str] = []
        if existing:
            # the album was fetched again while this sat in the bin: add what is missing, touch
            # nothing that is there, and say which tracks were left alone
            have = {t.video_id for t in existing.tracks}
            plan, album_dir = existing, album_dir
            for entry_id in list(wanted):
                binned = find_entry(self.library, entry_id)
                if binned and binned.data.get("track", {}).get("video_id") in have:
                    skipped.append(binned.data["track"].get("title", entry_id))
                    wanted.remove(entry_id)
        else:
            album_dir.mkdir(parents=True, exist_ok=True)
            plan.tracks = []                       # the tracks come back one entry at a time
            if cover := entry.cover:
                shutil.move(str(cover), album_dir / cover.name)
            save_plan(plan, album_dir)

        back = 0
        for entry_id in wanted:
            binned = find_entry(self.library, entry_id)
            if not binned or not binned.data.get("track"):
                continue
            plan = load_plan(album_dir) or plan
            if self._restore_track(binned, album_dir, plan).status == "ok":
                back += 1

        if not existing and not back and only is None:
            self.log(f"{plan.album}: the folder and plan are back, but no track entry was left in the bin")
        said = f" ({len(skipped)} already there: {', '.join(skipped[:3])})" if skipped else ""
        if only is None:
            self.log(f"restored {plan.albumartist} — {plan.album} with {back} track(s){said}")
        else:
            # the caller asked for one track and reports it itself; a track binned before the album
            # is not in the album entry's list at all, so counting here would only confuse
            self.log(f"rebuilt {plan.albumartist} — {plan.album} from the bin{said}")
        plan = load_plan(album_dir) or plan
        if only is None and not skipped:
            shutil.rmtree(entry.path, ignore_errors=True)
        return self.execute(plan, album_dir)

    def _restore_track(self, entry: Entry, album_dir: Path, plan: AlbumPlan) -> Outcome:
        """One track back into an album that exists — or repaired, where the plan never lost it."""
        assert self.library
        track = PlanTrack.from_dict(entry.data["track"])
        present = next((t for t in plan.tracks if t.video_id == track.video_id), None)
        displacer = (entry.data.get("ranking") or {}).get("chosen", {}).get("ref")
        # three shapes now: the track is gone; the track is there with no file (a repair); or the
        # track is there with *another file* — a merge took a copy from somewhere else, and putting
        # this one back means undoing that (§9, slice 54)
        undo = present is not None and bool(displacer) and present.effective_id == displacer
        repair = present is not None and not (album_dir / present.filename).is_file()
        if present is not None and not repair and not undo:
            return Outcome("failed", message=f"{track.title} is already in {plan.album}")
        if repair or undo:
            track = present                        # the plan's own copy stays authoritative
        if undo:
            if taken := _inside(album_dir, track.filename):
                taken.unlink(missing_ok=True)      # the copy that displaced this one goes
            track.source_override = None
            track.chosen = track.video_id
            track.ext = Path(entry.audio).suffix.lstrip(".") if entry.audio else track.ext
            track.filename = wanted_filename(plan, track)
            track.file_length = None               # measured again from the file coming back
            track.tagged = None

        album_dir.mkdir(parents=True, exist_ok=True)
        if (audio := entry.audio) and track.filename:
            back = album_dir / track.filename
            back.parent.mkdir(parents=True, exist_ok=True)  # a disc sub-folder may have gone with it
            shutil.move(str(audio), back)
        if original := entry.original:
            (album_dir / ORIGINALS).mkdir(exist_ok=True)
            shutil.move(str(original), album_dir / ORIGINALS / f"{trim_key(track.effective_id)}{original.suffix}")
        said = ""
        if words := entry.words:
            if read_sidecar(album_dir, track):
                said = " — your own lyrics were there, so the binned ones were left in the bin"
            else:
                shutil.move(str(words), sidecar_path(album_dir, track.filename))

        # Whatever displaced this file is not offered for the track again: the user has just said
        # they preferred what was here (spike §4). Until ranking lands (P52) `ranking` only ever
        # holds what was measured, never a verdict.
        if displacer := (entry.data.get("ranking") or {}).get("chosen", {}).get("ref"):
            if (present or track).refuse(displacer):
                self.log(f"{displacer} will not be offered for {track.title} again")
        if present is None:
            # Put it back where it stood, **with the number it had** (R-373). It used to be
            # renumbered through `arrange`, because deleting closed the gap behind it and restoring
            # a 1 into an album that already had a 1 gave two of them. Deleting leaves the gap now,
            # so the track's own number is the one that is free, and the album it comes back to is
            # the album it left. Sorting the whole list instead would interleave a multi-disc
            # album (slice 22), so only this one row is placed.
            same_disc = [i for i, t in enumerate(plan.tracks) if t.disc == track.disc]
            after = [i for i in same_disc if plan.tracks[i].number >= track.number]
            plan.tracks.insert(
                after[0] if after else (same_disc[-1] + 1 if same_disc else len(plan.tracks)), track)
            if any(t is not track and t.disc == track.disc and t.number == track.number
                   for t in plan.tracks):
                # **somebody took the number while it was in the bin**, which since R-373 can only be
                # the user's own reorder. Where the *files* state the numbers (R-375) the disc may not
                # be counted off for that: measured on an adopted rip of 01, 02, 04, restoring a
                # track whose file says `4/5` gave it 2. So it takes the lowest number still free on
                # its disc and **nobody else's number moves**; for an album noaap fetched, where the
                # position in the source is all a number ever was, the disc is counted again as before.
                if states_numbers(plan):
                    track.number = self._lowest_free(plan, track)
                    self.log(f"{track.title} came back as {track.number:02d}: the number it had was "
                             "taken while it was in the bin")
                else:
                    arrange(plan)
        save_plan(plan, album_dir)
        if not said:
            shutil.rmtree(entry.path, ignore_errors=True)
        what = "repaired" if repair else "restored"
        self.log(f"{what} {track.artist} - {track.title} in {plan.album}{said}")
        return self.execute(plan, album_dir)   # renames, retags, and fixes tracktotal

    def empty_recycle(self, older_than_days: float | None = None) -> Outcome:
        """Remove bin entries for good. Only ever because somebody asked."""
        from .recycle import empty as empty_bin

        if not self.library:
            return Outcome("failed", message="no library configured")
        gone, freed = empty_bin(self.library, older_than_days)
        self.log(f"removed {gone} recycle entr{'y' if gone == 1 else 'ies'} for good, {freed / 1e6:.1f} MB")
        return Outcome("ok", message=f"{gone} removed")

    def delete_album(self, source_id: str) -> Outcome:
        """Delete everything noaap put into this album folder, then the folder if it is empty."""
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        # Order: bin every track, then bin the album (plan + cover + the track entries' ids), then
        # remove what is left on disk. Binning first means an interrupted delete leaves the audio
        # safe and the plan still listing it — recoverable, and `restore` treats it as a repair. The
        # other order would leave a plan that has forgotten tracks whose files are already gone.
        ids = []
        for track in plan.tracks:
            if entry := self._bin(album_dir, plan, track, DELETED, audio=_inside(album_dir, track.filename)):
                ids.append(entry.name)
        if self.library:
            cover = next(iter(sorted(album_dir.glob("cover.*"))), None)
            bin_album(self.library, plan, cover, ids, DELETED)
            self.log(f"moved the album and {len(ids)} track(s) to the recycle bin")
        for path in [*album_dir.glob("cover.*"), album_dir / PLAN_FILE]:
            path.unlink(missing_ok=True)
        for folder in (album_dir / ORIGINALS, album_dir / PARTS_DIR):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        left = sorted(p.name for p in album_dir.iterdir()) if album_dir.exists() else []
        if left:
            self.log(f"kept {album_dir}: it still holds {len(left)} file(s) that are not ours ({', '.join(left[:3])})")
        else:
            album_dir.rmdir()
            with contextlib.suppress(OSError):
                album_dir.parent.rmdir()  # the artist folder, only when empty
            self.log(f"deleted {album_dir}")
        return Outcome("ok", plan, album_dir)

    # -- edits (web UI) ----------------------------------------------------------------

    def trim_channel(self, channel: str, start: float | None, end: float | None) -> list[Outcome]:
        """Same trim for every track from one uploader, across the whole library."""
        outcomes = []
        for album_dir, plan in iter_plans(self.library) if self.library and self.library.exists() else []:
            hits = [t for t in plan.tracks if (t.channel or "") == channel]
            if not hits:
                continue
            for t in hits:
                t.trim_start, t.trim_end = start, end
            save_plan(plan, album_dir)
            self.log(f"{plan.album}: {len(hits)} track(s) from {channel}")
            outcomes.append(self._guarded(lambda: self.execute(plan, album_dir)))
        if not outcomes:
            self.log(f"no tracks from {channel} in the library")
        return outcomes

    def find_album(self, source_id: str) -> tuple[Path, AlbumPlan] | None:
        return find_plan(self.library, source_id) if self.library and self.library.exists() else None

    def apply_edits(self, source_id: str, edits: dict[str, Any]) -> Outcome:
        """User edits from the UI: set values, mark them as the user's, then rename/retag on disk."""
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        before = {t.video_id: (t.filename, t.effective_id) for t in plan.tracks}
        apply_user_edits(plan, edits)
        for t in plan.tracks:
            old, took = before.get(t.video_id, (None, None))
            if old and old != t.filename and Path(old).suffix != Path(t.filename).suffix:
                stale = _inside(album_dir, old)  # a changed format leaves the old file behind
                if stale and stale.exists():
                    stale.unlink()
            if took and took != t.effective_id:
                # the original kept for the previous source is another recording: it can never be
                # what this track is cut from, and keeping it would only shadow the new one (§9, slice 34)
                for path in originals_of(album_dir, took):
                    path.unlink()
                self.log(f"{t.title}: audio now from {t.effective_id} (was {took})")
        album_dir = relocate(album_dir, plan, self.library, for_album(self.cfg, plan))
        return self.execute(plan, album_dir)


def _minutes(audio: Path) -> str:
    """How much audio a request is about to send, because a per-minute bill is the user's (§9, slice 37)."""
    try:
        seconds = measured_length(audio) or 0
    except Exception:
        return "unknown length"
    return f"{seconds / 60:.1f} min"


def _inside(album_dir: Path, filename: str) -> Path | None:
    """album_dir/filename, but only if that really is a path inside the album folder.

    **Inside it at any depth, not only directly in it** (§9, slice 61): an adopted album's files may
    sit in disc sub-folders and the plan says so by naming them relative to the album. A plan is a
    file on disk and a tampered one can name `../../something` or an absolute path, so the answer is
    only ever something that resolves within this album.
    """
    if not filename or Path(filename).is_absolute():
        return None
    root = album_dir.resolve()
    path = (album_dir / filename).resolve()
    return path if path != root and root in path.parents else None


def reset_field(obj: AlbumPlan | PlanTrack, name: str) -> bool:
    """Put one field back to what noaap derived, and stop calling it the user's.

    The value matters more than the mark: `_merge_fields` decides a field is the user's by comparing
    it with `auto` and re-asserts the USER provenance on every merge, so dropping the mark alone
    would be undone by the next update (DESIGN.md §9, slice 29). The provenance is dropped rather than
    guessed at — `auto` records the derived *value*, never where it came from — and the next pass
    that touches the field writes a truthful marker again.
    """
    if name == "order":
        return isinstance(obj, AlbumPlan) and obj.provenance.pop("order", None) is not None
    if name == "source":
        # the way back is the playlist's own video, which `auto` does not have to remember: it is
        # `video_id`. Going back costs what choosing cost — the track is fetched again (§9, slice 34).
        return isinstance(obj, PlanTrack) and switch_source(obj, None)
    editable = EDITABLE_ALBUM if isinstance(obj, AlbumPlan) else EDITABLE_TRACK
    if name not in editable or name not in obj.auto:
        return False  # nothing was derived for it, so there is nothing to go back to
    setattr(obj, name, obj.auto[name])
    obj.provenance.pop(name, None)
    return True


def switch_source(track: PlanTrack, video_id: str | None) -> bool:
    """Point a track at another video — or back at the playlist's — and forget the old audio.

    The playlist video stays the track's identity; only where the *audio* comes from changes
    (DESIGN.md §9, slice 34). Everything the old file was is therefore wrong at once: the state, because
    there is another recording to fetch; the tags written from it; the trim marks, which describe
    seconds of the old recording (the UI names them before it asks); the measured length; and the
    uploader and duration, which follow the audio rather than the identity.

    The lyrics *status* goes with it so the next pass looks the new length up again. The words
    never do: the user's stay theirs, and a sidecar whose timings were written for the old file
    says so in the panel until it is saved again (§9, slice 21 is untouched by this).
    """
    if (track.source_override or None) == (video_id or None):
        return False
    if video_id and video_id in track.refused_candidates:
        return False          # somebody already turned this one down for this track
    track.source_override = video_id
    if video_id and not track.candidate(video_id):
        track.candidates.append(Candidate(
            ref=video_id, added_by="user", why="chosen instead of the playlist's",
            when=datetime.now(UTC).date().isoformat()))
    track.sync_candidates()   # `chosen` follows; the old fields stay the truth
    if video_id:
        track.provenance["source"] = Provenance.USER
        track.auto["source"] = track.video_id  # what it goes back to, which P12's badge offers
    else:
        track.provenance.pop("source", None)
        track.auto.pop("source", None)
    track.state, track.error, track.error_kind = "pending", None, None
    track.tagged = track.trimmed = None
    track.trim_start = track.trim_end = None
    track.file_length = track.duration = track.channel = None
    track.lyrics = None
    return True


def apply_user_edits(plan: AlbumPlan, edits: dict[str, Any], source: Any = None) -> AlbumPlan:
    """Pure: copy editable fields from `edits` into the plan, marking changed ones as USER.

    `reset` (album-level, and per track) names fields to hand back to noaap; it is applied first,
    so a save that resets one field and edits another does both.

    `source` is only needed to read what the user typed into the audio-source field: the provider
    knows what one of its own refs looks like, and nothing here does (§9, slice 51).
    """
    for name in edits.get("reset") or []:
        reset_field(plan, str(name))
    for name in EDITABLE_ALBUM:
        if name in edits:
            value = edits[name]
            if name == "year":
                value = int(value) if str(value or "").strip().isdigit() else None
            elif not isinstance(value, str) or not value.strip():
                continue
            else:
                value = value.strip()
            if value != getattr(plan, name):
                setattr(plan, name, value)
                plan.provenance[name] = Provenance.USER
    by_id = {t.video_id: t for t in plan.tracks}
    was_on = {t.video_id: t.disc for t in plan.tracks}  # numbers count inside the disc they were on
    typed: dict[str, int] = {}  # tracks the user gave a new number to -> the position they typed
    discs_changed = order_changed = False
    for te in edits.get("tracks", []):
        t = by_id.get(te.get("video_id"))
        if not t:
            continue
        for name in te.get("reset") or []:
            reset_field(t, str(name))
        if "source" in te:
            wanted = str(te.get("source") or "").strip()
            whose = source or sources.for_plan(plan, Config())
            chosen = whose.one_ref(wanted) if wanted else None
            if wanted and chosen is None:
                raise ValueError(f"{t.title}: “{wanted}” is not a single YouTube video")
            switch_source(t, None if chosen == t.video_id else chosen)
        if take := str(te.get("take") or "").strip():
            # taking a copy the pass listed and could not rank (§9, slice 55). Only a ref already on
            # this track can be taken: the user is answering a question the pass asked, not naming a
            # new source — that is what the field above is for, and it is the provider's to parse.
            if not t.candidate(take):
                raise ValueError(f"{t.title}: “{take}” is not one of this track's known copies")
            switch_source(t, None if take == t.video_id else take)
        if refuse := str(te.get("refuse") or "").strip():
            # never offered for this track again, and if it is the one in use the track goes back to
            # the playlist's own video — refusing what you are listening to has to mean something
            if refuse == t.source_override:
                switch_source(t, None)
            t.refuse(refuse)
        if (choice := te.get("audio_choice")) in ("best", "combined") and choice != t.audio_choice:
            # switching means fetching the track again, in the other form
            t.audio_choice, t.ext = choice, "m4a" if choice == "combined" else "opus"
            t.state, t.error, t.error_kind, t.tagged, t.trimmed = "pending", None, None, None, None
        if "trim_start" in te or "trim_end" in te:
            start, end = parse_time(te.get("trim_start")), parse_time(te.get("trim_end"))
            if start is not None and end is not None and end <= start:
                raise ValueError(f"{t.title}: the end must come after the start")
            t.trim_start, t.trim_end = start, end
        if str(te.get("number", "")).strip().isdigit():
            wanted = max(1, int(te["number"]))
            # `moved` is the UI saying "the user put this row here", which a changed number cannot
            # always show: a row dragged into another disc often keeps its per-disc number by
            # coincidence (2-02 dropped at 1-02), and without the flag it would be read as a row
            # that stayed put and end up after that disc's rows instead of among them (§9, slice 32).
            if wanted != t.number or te.get("moved"):
                typed[t.video_id] = wanted
                order_changed = True
            t.number = wanted
        if str(te.get("disc", "")).strip().isdigit():
            disc = max(1, int(te["disc"]))
            discs_changed |= disc != t.disc
            t.disc = disc
        for name in EDITABLE_TRACK:
            value = te.get(name)
            if isinstance(value, str) and value.strip() and value.strip() != getattr(t, name):
                setattr(t, name, value.strip())
                t.provenance[name] = Provenance.USER
                if name == "title":
                    # this is no longer the recording MusicBrainz matched, and neither is its
                    # length: the two belong together, and a length that outlives its recording
                    # is a false reference that `repair` would later drop on its own
                    t.mbid = t.mb_length = None
    if order_changed:
        # the numbers first, read where they were typed: inside the disc the track was on, where
        # they are unique. Doing this against the *new* discs is what interleaved a collapse.
        plan.tracks = placed(plan.tracks, was_on, typed)
    if discs_changed or order_changed:
        arrange(plan)  # then the discs, keeping that arrangement, counting each disc from 1
    if order_changed:
        plan.provenance["order"] = Provenance.USER  # the source may not renumber this album
    return refresh_derived(plan)


def track_spelling(plan: AlbumPlan) -> str | None:
    """The spelling this album's own tracks carry, when it may speak for the album.

    The guards are §9, slice 23's: not an album artist the user chose, the same artist key (so case and
    punctuation only, never a genuinely different credit), the most common track credit, and
    MusicBrainz behind that credit. A tie between two equally common spellings is settled by the
    evidence and then by `spelling_rank`, because `max(set(names), key=names.count)` would settle
    it by set iteration order — which hash randomisation makes differ between runs.
    """
    if plan.provenance.get("albumartist") == Provenance.USER or not plan.tracks:
        return None
    counts = Counter(t.artist for t in plan.tracks)
    most = max(counts.values())
    confirmed = {t.artist for t in plan.tracks if t.provenance.get("artist") == Provenance.MB}
    common = min(
        (name for name, n in counts.items() if n == most),
        key=lambda n: (n not in confirmed, spelling_rank(n, {Provenance.MB} if n in confirmed else set())),
    )
    if common not in confirmed or text_key(common) != text_key(plan.albumartist):
        return None
    return common


def spelling_rank(name: str, sources: set[str | None],
                  albums: int = 0) -> tuple[bool, bool, bool, bool, int, int, str]:
    """How good a spelling is: what someone chose, then MusicBrainz, then case, then **how many
    albums hold it**, then length, then the alphabet.

    **The count comes before the alphabet** (§9, slice 129, R-478). Two casings of one name are the
    same length, so the last word used to go to `name` — and `'E' < 'e'`, so Title Case always won.
    On the user's library that chose `Umbra Et Imago` (4 albums) over `Umbra et Imago` (23) and
    `Subway To Sally` (1) over `Subway to Sally` (22): the artists' own spelling, in the lowercase
    German particle, rewritten to follow a handful of files that disagreed.
    **And the count is never evidence, only a tie-break** — the user: *"such stuff should follow
    MusicBrainz, not numbers"*. So USER and MusicBrainz still come first, the case penalties still
    come before it, and a spelling that wins on count alone is marked as such by its caller so a
    later lookup can replace it.
    """
    return (
        Provenance.USER not in sources,  # a spelling someone chose themselves
        Provenance.MB not in sources,  # then one MusicBrainz confirmed
        name.isupper(),  # then mixed case over a shouting channel name
        name.islower(),
        -albums,  # then the one most of the library already uses
        len(name),
        name,
    )


def spelling_basis(name: str, sources: set[str | None], names: dict[str, set[str | None]],
                   counts: dict[str, int] | None = None) -> str:
    """Why this spelling won: `user`, `MusicBrainz`, `count` or `alphabet` (R-479, point 2).

    Said in the check and written into the plan, because a choice nobody can account for is not a
    choice a reader can disagree with — and because `count` is the one basis a later MusicBrainz
    lookup is meant to overrule.
    """
    if Provenance.USER in sources:
        return "user"
    if Provenance.MB in sources:
        return "MusicBrainz"
    rivals = [n for n in names if n != name]
    if not rivals:
        return "the only spelling"
    counts = counts or {}
    if counts.get(name, 0) > max((counts.get(n, 0) for n in rivals), default=0):
        return "count"
    return "alphabet"


def placed(tracks: list[PlanTrack], was_on: dict[str, int], typed: dict[str, int]) -> list[PlanTrack]:
    """Put every track the user typed a number for on that position, inside the disc it belongs to.

    A typed number is a position, in both directions and including the last one. Sorting by the
    numbers cannot do that: a track moved *down* still sorts ahead of the track that holds the
    position below its target, so typing 5 on the first of five tracks moved it to 4 and no typed
    number could ever move a track to the end (DESIGN.md §9, slice 22). So the typed tracks are taken
    out of the arrangement and put back at the index they asked for, lowest number first, while
    the untouched ones keep their relative order.

    Which disc a number counts in depends on whether it was typed at all (§9, slice 32). A number the
    user gave counts in the disc they are putting the track on — that is what dragging a row into
    another disc means, and what typing a position after collapsing a split means. A number left
    alone counts where the track *was*, because after a disc change those numbers are the old
    per-disc ones and reading them under the new discs would interleave the two (§9, slice 22's collapse).
    """
    groups: dict[int, list[PlanTrack]] = {}
    for t in tracks:
        groups.setdefault(t.disc if t.video_id in typed else was_on.get(t.video_id, t.disc), []).append(t)
    for disc, group in groups.items():
        rest = [t for t in group if t.video_id not in typed]
        for t in sorted((x for x in group if x.video_id in typed), key=lambda x: typed[x.video_id]):
            rest.insert(min(typed[t.video_id] - 1, len(rest)), t)
        groups[disc] = rest
    return [t for disc in sorted(groups) for t in groups[disc]]


def arrange(plan: AlbumPlan) -> AlbumPlan:
    """Group the tracks by disc and count each disc from 1, in the order its tracks stand.

    The arrangement is the order the tracks are in — the order the album view shows — and this
    only groups and counts it. Sorting by `(disc, number)` as well, which is what this used to
    do, reshuffles an album whenever the numbers are not unique across the discs: collapsing
    1-01…1-03 / 2-01…2-03 back to one disc interleaved them (DESIGN.md §9, slice 22).
    """
    by_disc: dict[int, list[PlanTrack]] = {}
    for t in plan.tracks:
        by_disc.setdefault(t.disc, []).append(t)
    plan.tracks = [t for disc in sorted(by_disc) for t in by_disc[disc]]
    for group in by_disc.values():
        for number, t in enumerate(group, 1):
            t.number = number
    return plan


def exit_code(outcomes: list[Outcome] | Outcome) -> int:
    """CLI exit status: 3 = YouTube is blocking, 1 = something failed, 0 = fine."""
    items = outcomes if isinstance(outcomes, list) else [outcomes]
    if any(o.blocked for o in items):
        return 3
    return 1 if any(o.status in ("failed", "incomplete") for o in items) else 0


def collection_address(text: str, cfg: Config | None = None) -> str | None:
    """Where a collection lives, if that is what the user typed — a channel URL, or a folder.

    The core cannot tell; the provider can. This asks the default one, which is what the CLI and
    the page did through `channel_base_url` before there were providers.
    """
    settings = cfg or Config()
    for name in [sources.DEFAULT, *sources.known()]:
        provider = sources.get(name, settings)
        if not provider.handles(text):
            continue
        if reader := getattr(provider, "collection_url", None):
            return reader(text)
    return None


__all__ = ["Outcome", "Service", "apply_user_edits", "collection_address", "exit_code"]
