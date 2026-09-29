"""What makes two files the same recording (DESIGN §9, slice 66).

A plan names a track's file. When that file is not there any more — renamed, moved into a disc folder,
or the whole intake folder it came from moved — the only way back is to ask what a file *holds* rather
than what it is called. This is that question, and the one place it is answered.

**It decodes.** The cheaper digest (`sources_folder.stream_sha`) copies the packets instead, agrees with
this over every untouched file of the reference collection, and is three times faster — and it is still
the wrong instrument: it includes a trailing ID3v1 or Lyrics3v2 block, because the demuxer hands those
over as audio data, so a file and a re-tagged copy of it read as two recordings (14 of 22 real pairs in
one album, catalogue BD8). Equal packets do imply equal audio, so it survives here as a **pre-check in
that one direction** and never as an answer to "different".

**An identity carries its maker.** Another build of ffmpeg may decode a lossy file to other samples, so
two are compared only when `audio_sha_by` agrees, and an identity from another maker is measured again
rather than trusted (R-227, ruling 1).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .models import Candidate, PlanTrack
from .sources_folder import stream_sha
from .tag import audio_length, decoded_sha, decoder

CLOSE = 0.1  # seconds two files' lengths may differ by and still be worth decoding


@dataclass(frozen=True)
class Identity:
    """What a file holds, and who says so."""

    sha: str
    by: str

    def same_as(self, other: Identity | None) -> bool:
        """**Only on equal terms**: two identities made by different decoders are not compared."""
        return other is not None and self.by == other.by and self.sha == other.sha


def of(path: Path) -> Identity | None:
    """Measure one file. `None` where ffmpeg could not be asked — which is never an answer of "no"."""
    sha = decoded_sha(path)
    return Identity(sha, decoder()) if sha else None


def recorded(copy: Candidate) -> Identity | None:
    """What a candidate already says about itself, if it was measured by the decoder we have now."""
    if not copy.audio_sha or not copy.audio_sha_by:
        return None
    return Identity(copy.audio_sha, copy.audio_sha_by) if copy.audio_sha_by == decoder() else None


def remember(copy: Candidate, found: Identity) -> None:
    copy.audio_sha, copy.audio_sha_by = found.sha, found.by


def identity_of(track: PlanTrack, path: Path) -> Identity | None:
    """The track's own identity: what it recorded, or a fresh measurement of the file it names.

    Measured once per file and written down, so a second pass over the same library costs nothing.
    """
    for copy in track.candidates:
        if copy.ref == track.effective_id and (known := recorded(copy)) is not None:
            return known
    if not path.is_file():
        return None
    if (found := of(path)) is None:
        return None
    for copy in track.candidates:
        if copy.ref == track.effective_id:
            remember(copy, found)
    return found


def worth_decoding(want: float | None, path: Path) -> bool:
    """The guard before the expensive test: a file that moved still has the length it had.

    A length is read from the header in a millisecond; decoding costs 139 ms for a flac and 455 for an
    Opus file. Where either length is unknown the file stays a candidate — unknown is not "no".
    """
    here = audio_length(path)
    return want is None or here is None or abs(here - want) <= CLOSE


def matches(want: Identity | None, length: float | None, files: Iterable[Path],
            cheap: str | None = None) -> tuple[list[Path], int]:
    """Every file that holds this recording, and **how many files had to be decoded to say so**.

    Shortlisted by length first. Two things can answer, and either is enough:

    * `cheap`, the packet digest the plan recorded for the file — **a file that was renamed or moved is
      byte for byte what it was**, so this finds it for nothing. It is the common case and it costs no
      decode at all.
    * `want`, the identity, for a file that was also rewritten since it was last measured: a re-tag
      changes the packet digest and not the recording.

    The count is returned rather than logged because what a pass costs is a number somebody asked for.
    """
    found, decoded = [], 0
    for path in files:
        if not worth_decoding(length, path):
            continue
        if cheap and stream_sha(path) == cheap:
            found.append(path)
            continue
        if want is None:
            continue
        decoded += 1
        if want.same_as(of(path)):
            found.append(path)
    return found, decoded


__all__ = ["CLOSE", "Identity", "identity_of", "matches", "of", "recorded", "remember", "worth_decoding"]
