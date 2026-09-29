"""What makes two files the same recording (DESIGN §9, slice 66).

The measurement behind this file: `stream_sha` called **14 of 22** copies of one real album different
from the files they had been copied from, while the decoded audio was identical. The cause, to the byte:
7699 of 7700 packets identical and the last one 52 bytes against 180 — the difference being exactly the
**128-byte trailing ID3v1 tag** that ffmpeg's demuxer hands over as audio data, and that mutagen dropped
when noaap tagged the copy. So the first case here is that file, built by hand.
"""
from __future__ import annotations

import shutil

import pytest
from test_folder_source import encode

from noaap import identity
from noaap.models import Candidate, PlanTrack
from noaap.sources_folder import stream_sha
from noaap.tag import decoded_sha, decoder


def a_track(ref: str, filename: str) -> PlanTrack:
    track = PlanTrack(video_id=ref, number=1, artist="A Band", title="One", filename=filename,
                      provenance={}, state="done")
    track.sync_candidates()
    return track


# -- the cause, as a case ---------------------------------------------------------------------------


def test_a_trailing_id3v1_makes_the_cheap_digest_lie(tmp_path):
    """A file with an ID3v1 tag at the end, and the same file without one: one recording, two digests.

    This is BD8 in miniature, and it is why the identity decodes.
    """
    from mutagen.id3 import ID3

    original = encode(tmp_path / "with-id3v1.mp3", title="One", artist="A Band")
    with original.open("ab") as out:                      # 128 bytes of ID3v1, as a tagger of 2003 left it
        out.write(b"TAG" + b"One".ljust(30) + b"A Band".ljust(30) + b" " * 60 + b"\x00\x00")
    copy = tmp_path / "rewritten.mp3"
    shutil.copy2(original, copy)
    ID3(copy).save(copy)                                  # mutagen rewrites the file and drops the ID3v1

    assert copy.stat().st_size != original.stat().st_size
    assert stream_sha(copy) != stream_sha(original), "the cheap digest reads two recordings"
    assert decoded_sha(copy) == decoded_sha(original), "and there is one"
    assert identity.of(copy).same_as(identity.of(original))


def test_equal_packets_are_believed_without_decoding(tmp_path):
    """The one direction the cheap digest may be used in: a file byte-identical in its packets holds
    the same audio, so `matches` takes it without paying for a decode."""
    one = encode(tmp_path / "one.mp3", title="One", artist="A Band")
    same = tmp_path / "same.mp3"
    shutil.copy2(one, same)

    found, decoded = identity.matches(identity.of(one), None, [same], cheap=stream_sha(one))

    assert found == [same] and decoded == 0, "believed on the cheap digest, nothing decoded"


# -- and the terms two identities are compared on ---------------------------------------------------


def test_two_identities_are_compared_only_when_the_makers_agree():
    """R-227, ruling 1. Another build may decode a lossy file to other samples, so an identity from a
    decoder we do not have says nothing — and saying nothing is not saying no."""
    mine = identity.Identity("abc", "decoded/ffmpeg 8.0")

    assert mine.same_as(identity.Identity("abc", "decoded/ffmpeg 8.0"))
    assert not mine.same_as(identity.Identity("abc", "decoded/ffmpeg 7.1")), "same digest, other maker"
    assert not mine.same_as(identity.Identity("def", "decoded/ffmpeg 8.0"))
    assert not mine.same_as(None)


def test_an_identity_from_another_maker_is_measured_again(tmp_path):
    """Never trusted, and never taken for a mismatch either: it is measured."""
    one = encode(tmp_path / "one.mp3", title="One", artist="A Band")
    track = a_track(str(one), "one.mp3")
    track.candidates[0].audio_sha, track.candidates[0].audio_sha_by = "stale", "decoded/ffmpeg 1.0"

    found = identity.identity_of(track, one)

    assert found is not None and found.by == decoder() and found.sha != "stale"
    assert track.candidates[0].audio_sha == found.sha, "and what it measured is written down"


def test_what_a_candidate_already_says_is_reused(tmp_path):
    """Measured once per file: the second pass over a library costs nothing."""
    one = encode(tmp_path / "one.mp3", title="One", artist="A Band")
    track = a_track(str(one), "one.mp3")
    identity.remember(track.candidates[0], identity.Identity("written-down", decoder()))

    assert identity.identity_of(track, one).sha == "written-down"
    assert identity.identity_of(track, tmp_path / "gone.mp3").sha == "written-down", \
        "and it does not need the file to say it"


def test_a_track_whose_file_is_gone_and_was_never_measured_says_nothing(tmp_path):
    track = a_track("abc", "gone.mp3")

    assert identity.identity_of(track, tmp_path / "gone.mp3") is None


# -- the guard before the expensive test ------------------------------------------------------------


def test_only_files_of_the_right_length_are_decoded(tmp_path):
    """A file that moved still has the length it had. 139 ms for a flac, 455 for an Opus file — the
    length is read from the header in a millisecond, so it goes first."""
    one = encode(tmp_path / "one.mp3", title="One", artist="A Band")

    assert identity.worth_decoding(31.0, one) is True
    assert identity.worth_decoding(31.05, one) is True, "within a tenth of a second"
    assert identity.worth_decoding(12.0, one) is False
    assert identity.worth_decoding(None, one) is True, "unknown is not no"


def test_matches_finds_the_one_file_that_holds_it(tmp_path):
    other = encode(tmp_path / "other.mp3", hz=880, title="Two", artist="A Band")
    one = encode(tmp_path / "one.mp3", title="One", artist="A Band")
    moved = tmp_path / "moved" / "one under another name.mp3"
    moved.parent.mkdir()
    shutil.copy2(one, moved)

    found, decoded = identity.matches(identity.of(one), 31.0, [other, moved])

    assert found == [moved]
    assert decoded == 2, "both were the right length, so both had to be decoded — and that is the cost"


@pytest.mark.parametrize("sha,by", [("abc", None), (None, "decoded/x"), (None, None)])
def test_half_a_record_is_no_record(sha, by):
    assert identity.recorded(Candidate(ref="x", audio_sha=sha, audio_sha_by=by)) is None
