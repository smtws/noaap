"""The one data model (DESIGN.md §6).

A `Collection` is what YouTube says; an `AlbumPlan` is what we will write to disk.
Both are plain dataclasses that round-trip through JSON. "Entries in the playlist"
and "tracks on the album" are different lists and are never conflated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from typing import Any

PLAN_SCHEMA = 1


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
    MB = "mb"
    YT_MUSIC = "yt_music"  # yt-dlp's artist/track/album fields
    YT_TITLE = "yt_title"  # derived from the video title / channel name
    PLAYLIST = "playlist"  # derived from the playlist title / owner
    USER = "user"


@dataclass
class SourceRef:
    """A collection found on a channel, not fetched yet."""

    url: str
    source_id: str
    title: str
    tab: str  # "releases" (official albums), "playlists", "ytmusic" or "search"
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
    skipped: str | None = None  # reason, if this entry is unusable — the provider's own words
    skipped_kind: str | None = None  # and which `Failure` that is, for the pipeline to branch on
    transient: bool = False  # the reason may go away (bot check, network): the entry is still in the source

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


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
        entries = [Entry(**{**e, "music": Music(**e.get("music", {}))}) for e in d["entries"]]
        return cls(**{**d, "entries": entries})

    @property
    def unreadable(self) -> list[Entry]:
        """Entries that failed for a temporary reason: the collection is incomplete right now."""
        return [e for e in self.entries if e.transient]


# Forward compatibility, and it is a real risk rather than a theoretical one: two ytalbums share a
# library (the desktop app and a terminal), and a plan written by the newer one is read by the older
# every time. Before this, an unknown key raised TypeError on load; the alternative — dropping it —
# would have been worse, because the newer ytalbum would then silently lose a field it had written.
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
    added_by: str = "source"             # source | user | pass
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

    def sync_candidates(self) -> None:
        """Keep `candidates`/`chosen` in step with `video_id`/`source_override`.

        **The old fields are the truth.** Two ytalbums share a library, and an older one writes
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

    def candidate(self, ref: str) -> Candidate | None:
        return next((c for c in self.candidates if c.ref == ref), None)

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
    schema: int = PLAN_SCHEMA

    @property
    def is_compilation(self) -> bool:
        return self.kind == Kind.COMPILATION

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["tracks"] = [t.to_dict() for t in self.tracks]
        return {**out, **kept(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AlbumPlan:
        if d.get("schema") != PLAN_SCHEMA:
            raise ValueError(f"unsupported plan schema {d.get('schema')!r} (expected {PLAN_SCHEMA})")
        plan = keeping(cls, {**d, "tracks": [PlanTrack.from_dict(t) for t in d["tracks"]]})
        return plan
