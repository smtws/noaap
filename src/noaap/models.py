"""The one data model (DESIGN.md §6).

A `Collection` is what YouTube says; an `AlbumPlan` is what we will write to disk.
Both are plain dataclasses that round-trip through JSON. "Entries in the playlist"
and "tracks on the album" are different lists and are never conflated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from typing import Any

PLAN_SCHEMA = 2
# What this version can read. A plan is schema 2 only when it actually holds a path written
# relative to its own folder; everything else stays 1 and is byte-for-byte what it always was
# (§9, slice 60).
PLAN_SCHEMAS = (1, 2)


class Kind(StrEnum):
    OFFICIAL_ALBUM = "official_album"
    ARTIST_PLAYLIST = "artist_playlist"
    COMPILATION = "compilation"
    SINGLE = "single"


class Failure(StrEnum):
    """Why something could not be had, in words no provider owns (DESIGN §9, slice 51).

    The *message* stays whatever the provider said — it is what a person reads. This is the part
    the pipeline branches on, so that `service.py` never compares against a YouTube string again.
    """

    BOT_CHECK = "bot_check"            # the source wants a sign-in before it will answer
    TRANSIENT = "transient"            # network, rate limit: the same request may work later
    NO_AUDIO_STREAM = "no_audio_stream"  # it is there, but not as audio we can take
    NOT_SUPPORTED = "not_supported"    # this provider cannot do anything with that reference


class Provenance(StrEnum):
    """Where a value came from.

    **The strings are what is on disk and they do not change.** `yt_music` and `yt_title` were named
    after the only provider there was; the names in code say what they mean for any of them, and the
    old names remain as aliases so nothing that reads a plan has to be rewritten (§9, slice 51).
    """

    MB = "mb"
    SOURCE_TAGS = "yt_music"    # metadata the source itself carried, beside the audio
    SOURCE_TITLE = "yt_title"   # read out of the item's title, by that source's conventions
    COLLECTION = "playlist"     # derived from the collection's own title or its owner
    USER = "user"

    # a folder's three origins (§9, slice 53). `FILE_TAGS` is the same *kind* of evidence as
    # `SOURCE_TAGS` — metadata carried beside the audio — but it is worth telling apart on disk:
    # whoever tagged that collection is not YouTube Music, and a reader of a plan should see which.
    FILE_TAGS = "file_tags"
    FOLDER_NAME = "folder_name"
    FILE_NAME = "file_name"

    # what these were called while YouTube was the only source
    YT_MUSIC = SOURCE_TAGS
    YT_TITLE = SOURCE_TITLE
    PLAYLIST = COLLECTION


@dataclass
class SourceRef:
    """A collection found on a channel, not fetched yet."""

    url: str
    source_id: str
    title: str
    tab: str  # "releases" (official albums), "playlists", "ytmusic", "search" or "folders"
    artist: str | None = None  # who YouTube says made it (search hits only)
    channel_url: str | None = None  # the channel that uploaded its tracks
    count: int | None = None  # entries, when YouTube told us
    thumbnail: str | None = None


@dataclass
class Music:
    """yt-dlp's music metadata for one video. Only filled by full extraction."""

    artist: str | None = None
    track: str | None = None
    album: str | None = None
    year: int | None = None


@dataclass
class Entry:
    video_id: str
    position: int  # 1-based position in the source playlist
    title: str
    channel: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    chapters: list[dict[str, Any]] = field(default_factory=list)
    music: Music = field(default_factory=Music)
    disc: int = 1  # a source that knows its discs says so; a flat one leaves it at 1 (§9, slice 53)
    # The container this entry's audio is in, when the source knows before fetching it — a folder
    # always does, since the file is already there (§9, slice 58). `None` means "find out when it
    # arrives", which is what a download does. **The core may not read it off the ref**: a ref is
    # opaque, and the one time it was parsed for a suffix it renamed 1662 real files into a lie.
    ext: str | None = None
    # Where this entry's audio is, **relative to the collection**, when the source knows — a folder
    # does, and `cd1/…` is the answer for a disc in a sub-folder (§9, slice 61). `None` means the
    # source cannot say, which is every source that has to fetch before there is a file.
    # **The same boundary as `ext`, broken the same way:** adoption read the path out of the ref, kept
    # only its `.name`, and every later pass then copied 67 real files into their album roots.
    where: str | None = None
    # every copy of this recording the source can offer, best first — `video_id` is the first.
    # A folder holding an album twice, once as flac and once as mp3, is the case this is for.
    copies: list[Candidate] = field(default_factory=list)
    skipped: str | None = None  # reason, if this entry is unusable — the provider's own words
    skipped_kind: str | None = None  # and which `Failure` that is, for the pipeline to branch on
    transient: bool = False  # the reason may go away (bot check, network): the entry is still in the source

    @property
    def owner(self) -> str | None:
        """Who published this one — the provider's `channel`, under the name the core uses."""
        return self.channel

    # `url` used to live here, building a watch link from the video id. It had no callers left, and
    # a link is the provider's to build (§9, slice 51): `sources.Source.url_for(ref)`.


@dataclass
class Collection:
    source_url: str
    source_id: str  # playlist id or video id
    is_playlist: bool
    title: str
    channel: str | None
    thumbnail: str | None
    fetched_at: str
    entries: list[Entry]
    modified: str | None = None  # when YouTube last changed the playlist (YYYYMMDD)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Collection:
        entries = [Entry(**{**e, "music": Music(**e.get("music", {})),
                            "copies": [Candidate(**c) for c in e.get("copies") or []]})
                   for e in d["entries"]]
        return cls(**{**d, "entries": entries})

    @property
    def owner(self) -> str | None:
        """Whoever publishes this collection — a channel on YouTube, a folder's name elsewhere.

        `channel` is the provider's word and stays on disk; `owner` is what the classifier reads
        (§9, slice 51), so that "how many artists, and does the collection have an owner" does not
        have to be asked in YouTube's vocabulary.
        """
        return self.channel

    @property
    def unreadable(self) -> list[Entry]:
        """Entries that failed for a temporary reason: the collection is incomplete right now."""
        return [e for e in self.entries if e.transient]


# Forward compatibility, and it is a real risk rather than a theoretical one: two noaaps share a
# library (the desktop app and a terminal), and a plan written by the newer one is read by the older
# every time. Before this, an unknown key raised TypeError on load; the alternative — dropping it —
# would have been worse, because the newer noaap would then silently lose a field it had written.
# So what we do not understand is carried through untouched, and written back where it was.
_KEPT = "_unknown_fields"


def keeping(cls: Any, d: dict[str, Any]) -> Any:
    """Build a dataclass from `d`, remembering any key the class does not have."""
    known = {f.name for f in fields(cls)}
    made = cls(**{k: v for k, v in d.items() if k in known})
    extra = {k: v for k, v in d.items() if k not in known}
    if extra:
        object.__setattr__(made, _KEPT, extra)
    return made


def kept(obj: Any) -> dict[str, Any]:
    """The keys that were carried through, to write back beside the ones we understand."""
    return getattr(obj, _KEPT, {})


@dataclass
class Candidate:
    """One place this recording can be had from (DESIGN §9, slice 50).

    `ref` is **opaque**: only the provider that minted it knows what it means. Today every ref is a
    YouTube video id, which is why `provider` defaults to youtube — but nothing outside the provider
    may parse one, because that is the assumption the Source boundary exists to remove.
    """

    ref: str
    provider: str = "youtube"
    length: float | None = None          # seconds, as measured or as the source reported
    codec: str | None = None             # the four below are measured from a file we have
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    length_by: str | None = None         # "decoded" when the container would not say (§9, slice 53)
    cutoff_khz: int | None = None        # where this file's audio stops, measured (§9, slice 54)
    full_band: bool | None = None        # …and whether that is all its sample rate allows
    bytes: int | None = None             # how big the file is, where there is a file
    # a digest of the file as ffmpeg re-muxes it (§9, slice 53). **Not an identity**: it carries a
    # trailing ID3v1 tag, so a file and a re-tagged copy of it read as two recordings. Kept because it
    # is cheap and because equal-here implies equal-audio, which is a pre-check in one direction.
    stream_sha: str | None = None
    # **what "the same recording" means** (§9, slice 66): a digest of the decoded audio, with the
    # decoder that produced it. Two are compared only when the makers agree — another build may decode
    # a lossy file to other samples — and an identity from another maker is measured again, not trusted.
    # Filled the first time a pass needs identity, not during a collection read: that would take every
    # `adopt` of 2000 files from 167 s to 700 s for a question those passes never ask.
    audio_sha: str | None = None
    audio_sha_by: str | None = None
    added_by: str = "source"             # source | user | pass
    # a copy the merge pass found and could not rank against the one in use (§9, slice 55). It is
    # listed and nothing was copied: the pass had two files' numbers and no reason to prefer either,
    # so the decision is a person's. `why` carries the verdict's own sentence, unchanged.
    undecided: bool = False
    why: str = ""
    when: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanTrack:
    video_id: str
    number: int
    artist: str
    title: str
    # **the track's own file, as a path relative to the album folder** (§9, slice 61). For everything
    # noaap downloads that is a bare name and always was. For an adopted album it is whatever the
    # owner's layout says, `cd1/…` included — a plan that can only hold a bare name cannot say where
    # a disc-folder track lives, and every pass then "found" the file missing and copied it into the
    # album root under a name of noaap's own.
    filename: str
    provenance: dict[str, str]  # field name -> Provenance
    disc: int = 1
    state: str = "pending"  # pending | done | failed
    error: str | None = None
    error_kind: str | None = None  # a `Failure`; "no_audio_stream" is the one the UI acts on
    audio_choice: str = "best"  # "best" = separate audio stream; "combined" = take it from the video
    ext: str = "opus"  # "m4a" when the audio was taken from a combined stream (copied, not re-encoded)
    # values as derived automatically; a field that differs from these was edited by the user
    auto: dict[str, str] = field(default_factory=dict)
    in_source: bool = True  # False once the video has left the source playlist
    tagged: str | None = None  # signature of the tags last written to the file
    mbid: str | None = None  # MusicBrainz recording id
    channel: str | None = None  # who uploaded it (for "trim everything from this channel")
    trim_start: float | None = None  # seconds cut from the front (label idents …)
    trim_end: float | None = None  # play only up to here (previews, outros)
    trimmed: str | None = None  # the trim actually applied to the file on disk
    mb_length: float | None = None  # seconds, as MusicBrainz knows the recording
    duration: float | None = None  # seconds of the video itself (YouTube)
    lyrics: str | None = None  # synced | plain | instrumental | none; None = not looked up yet
    lyrics_id: int | None = None  # the lrclib entry the text came from
    lyrics_length: float | None = None  # seconds of the recording lrclib matched (or refused on)
    lyrics_sha: str | None = None  # of the sidecar bytes *we* wrote; anything else is the user's
    lyrics_rejected: list[int] = field(default_factory=list)  # lrclib entries the user said are not this song
    file_length: float | None = None  # seconds of audio actually on disk, after any trim
    file_length_by: str | None = None  # "header", or "decoded" when the file would not say (§9, slice 56)
    # what this file was called and what it said when noaap adopted it, so an undo can give it
    # back (§9, slice 58). `adopted_tags` holds only the fields noaap would overwrite, each with
    # the value that was there — or None where the file said nothing.
    adopted_name: str | None = None
    adopted_tags: dict[str, Any] | None = None
    # another video to take the audio from, when the playlist's is not the recording you want
    # (a film cut, a live intro). The playlist video stays the track's identity (DESIGN.md §9, slice 34).
    source_override: str | None = None
    lyrics_for_source: str | None = None  # the video the sidecar's timings were written against
    lyrics_for_length: float | None = None  # and the length of the file at that moment
    lyrics_timed_by: str | None = None  # "local/WAV2VEC2…" when a provider placed the stamps (§9, slice 36)
    lyrics_words_by: str | None = None  # and when a provider *drafted the words* themselves (§9, slice 37)
    # what was given back to lrclib, and when: {"at": ISO, "sha": of the bytes sent} (§9, slice 42). Kept so
    # the same words are never offered for publishing twice — a publish cannot be taken back.
    lyrics_published: dict[str, str] | None = None
    # what the two methods' own answers looked like when a cross-checked alignment was saved
    # (§9, slice 44): both spans, both piling figures, and which method lost the song if either did. The
    # library then accumulates the evidence sixteen tracks cannot give, one saved alignment at a time.
    lyrics_checked: dict[str, str] | None = None
    # an lrclib entry that is nearly this recording, and what an alignment made of it (§9, slice 46):
    # {entry, ours, theirs, span, unplaced, decided, why}. Kept so a pass never asks twice and a
    # user who disagrees with a verdict has the numbers in front of them.
    lyrics_fit: dict[str, str] | None = None
    # Every place this recording can be had from, and which one is in use (§9, slice 50). Derived
    # from `video_id`/`source_override` for a plan written before they existed, and kept in step
    # with them afterwards — see `sync_candidates` for which is the truth when they disagree.
    candidates: list[Candidate] = field(default_factory=list)
    chosen: str | None = None            # the ref in use; None means "the first one"
    # refs that were tried and rejected for this track, never offered or auto-chosen again. The
    # `lyrics_rejected` shape (§9, slice 27), for the same reason: an answer somebody has already
    # turned down should not keep coming back.
    refused_candidates: list[str] = field(default_factory=list)
    # the date a near-miss pass asked lrclib and was told there is nothing else for this title
    # (§9, slice 46). Deliberately NOT a `lyrics_fit` verdict: there is no entry, so there is nothing for a
    # person to decide and the panel says nothing. It exists so a later pass can skip the lookup —
    # 878 of this library's 1089 wordless tracks are in this state, and they were asked every run.
    lyrics_no_entry: str | None = None

    def __post_init__(self) -> None:
        # every track is self-consistent from birth, however it was built — from a plan on disk, by
        # `build_plan`, or in a test — so a round trip through JSON is an identity
        self.sync_candidates()

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), **kept(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanTrack:
        return keeping(cls, {**d, "candidates": [Candidate(**c) for c in d.get("candidates") or []]})

    @property
    def owner(self) -> str | None:
        """Who published this track's audio — the provider's `channel`, under the core's name."""
        return self.channel

    def sync_candidates(self) -> None:
        """Keep `candidates`/`chosen` in step with `video_id`/`source_override`.

        **The old fields are the truth.** Two noaaps share a library, and an older one writes
        `source_override` without knowing `candidates` exists — so where they disagree the legacy
        fields win and the candidate list is rebuilt from them. That also makes this the synthesis
        step for every plan written before slice 50: a plan with no candidates gets one for its
        video, and a second for its override if it has one.
        """
        refs = {c.ref for c in self.candidates}
        for ref, added, why in ((self.video_id, "source", "the playlist's own video"),
                                (self.source_override, "user", "chosen instead of the playlist's")):
            if ref and ref not in refs:
                self.candidates.append(Candidate(ref=ref, added_by=added, why=why))
                refs.add(ref)
        self.chosen = self.source_override or self.video_id or None

    def provider_in(self, plan: AlbumPlan) -> str:
        """Whose audio this track's file is — its chosen candidate's provider, else the album's."""
        found = self.candidate(self.effective_id)
        return (found.provider if found else None) or plan.provider


    def candidate(self, ref: str) -> Candidate | None:
        return next((c for c in self.candidates if c.ref == ref), None)

    def undecided_copies(self) -> list[Candidate]:
        """Copies a pass found and could not rank, still waiting for a person (§9, slice 55).

        Both answers empty this list and nothing else does: taking one makes it the chosen ref,
        refusing one puts it in `refused_candidates` for good. That is why the library page can
        count it — a number that only a person can bring down.
        """
        chosen = self.effective_id
        return [c for c in self.candidates
                if c.undecided and c.ref != chosen and c.ref not in self.refused_candidates]

    def refuse(self, ref: str) -> bool:
        """Never offer this one for this track again (§9, slice 50)."""
        if not ref or ref in self.refused_candidates:
            return False
        self.refused_candidates.append(ref)
        return True

    @property
    def effective_id(self) -> str:
        """The video the audio comes from: the playlist's, unless the user chose another one.

        Everything about *identity* — ordering, `in_source`, prune, merge, the MusicBrainz match —
        keeps looking at `video_id`. Everything about the *audio* — the download, the kept
        original, the uploader whose trim applies — asks for this one.
        """
        return self.source_override or self.video_id


@dataclass
class AlbumPlan:
    source_url: str
    source_id: str
    kind: str
    album: str
    albumartist: str
    year: int | None
    cover_url: str | None
    folder: str  # relative to the library root
    tracks: list[PlanTrack]
    skipped: list[dict[str, str]] = field(default_factory=list)  # {video_id, title, reason}
    provenance: dict[str, str] = field(default_factory=dict)
    auto: dict[str, object] = field(default_factory=dict)  # album-level twin of PlanTrack.auto
    mbid: str | None = None  # MusicBrainz release id
    cover_fallback_url: str | None = None  # tried when cover_url fails (e.g. no Cover Art Archive image)
    cover_fetched: dict[str, str] = field(default_factory=dict)  # {url, sha1} of the cover.* we saved
    # what the source looked like last time: lets an update skip it after one cheap request
    source_state: dict[str, Any] = field(default_factory=dict)  # {"ids": [...], "modified": "YYYYMMDD"}
    # which provider this album's audio comes from (§9, slice 51). Absent means it was written
    # before providers existed, which can only mean YouTube.
    provider: str = "youtube"
    # **This album is the collection's, not ours** (§9, slice 58). An album adopted where it
    # already stood keeps the names its owner gave it and the tags they wrote, and renaming or
    # retagging it is a separate thing a person asks for — so no ordinary pass may do either.
    # Both default to false, which is every album noaap fetched itself.
    keep_names: bool = False   # do not rename its files and do not move its folder
    keep_tags: bool = False    # write nothing into its audio files, by any pass
    # what it looked like when noaap adopted it, so it can be given back (§9, slice 58):
    # {"folder": <path at adoption>, "at": <ISO date>}. Per track, `adopted_name`/`adopted_tags`.
    adopted: dict[str, Any] = field(default_factory=dict)
    schema: int = PLAN_SCHEMA

    @property
    def is_compilation(self) -> bool:
        return self.kind == Kind.COMPILATION

    def to_dict(self) -> dict[str, Any]:
        # **before the tracks are written, not only after they are read** (§9, slice 60): a track
        # synthesises the candidate for its own `video_id` and has to default it to youtube, because
        # a track cannot know. `from_dict` has always corrected that on load — so a plan built in
        # memory and saved once, which is every `adopt --apply`, left a folder album's candidates
        # claiming YouTube in the file until something re-read and re-saved it. Found by the
        # round-trip check itself: what is written must be what a load gives back.
        self.own_the_candidates()
        out = asdict(self)
        out["tracks"] = [t.to_dict() for t in self.tracks]
        return {**out, **kept(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AlbumPlan:
        if d.get("schema") not in PLAN_SCHEMAS:
            raise ValueError(f"unsupported plan schema {d.get('schema')!r} "
                             f"(this version reads {', '.join(str(s) for s in PLAN_SCHEMAS)})")
        plan = keeping(cls, {**d, "tracks": [PlanTrack.from_dict(t) for t in d["tracks"]]})
        plan.own_the_candidates()
        return plan

    def own_the_candidates(self) -> None:
        """A candidate synthesised from `video_id`/`source_override` belongs to this plan's provider.

        A track alone cannot know: `PlanTrack` builds those two from fields that say nothing about
        where they came from, and defaults them to youtube. The plan does know, and says so here.
        Candidates added deliberately — with their own provider, from another one — are left alone,
        which is what lets one album hold tracks from two providers (§9, slices 50 and 51).

        **Only the synthesised one is claimed.** `sync_candidates` marks what it invents
        `added_by="source"`; anything else was put there by someone who knew where it came from —
        the user, or a pass that measured it. An earlier version claimed whatever the
        `source_override` named, which quietly rewrote a merged-in track's provider to the album's
        on the next load, so a re-download of it would have asked YouTube for a folder path
        (§9, slice 54).
        """
        for track in self.tracks:
            for candidate in track.candidates:
                if candidate.ref == track.video_id and candidate.added_by == "source":
                    candidate.provider = self.provider
