"""What makes two files the same recording (DESIGN §9, slice 66).

The measurement behind this file: `stream_sha` called **14 of 22** copies of one real album different
from the files they had been copied from, while the decoded audio was identical. The cause, to the byte:
7699 of 7700 packets identical and the last one 52 bytes against 180 — the difference being exactly the
**128-byte trailing ID3v1 tag** that ffmpeg's demuxer hands over as audio data, and that mutagen dropped
when noaap tagged the copy. So the first case here is that file, built by hand.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
from test_folder_source import encode

from noaap import identity
from noaap.models import Candidate, PlanTrack
from noaap.sources_folder import stream_sha
from noaap.tag import decoded_sha, decoder, ffmpeg_audio


def a_track(ref: str, filename: str) -> PlanTrack:
    track = PlanTrack(video_id=ref, number=1, artist="A Band", title="One", filename=filename,
                      provenance={}, state="done")
    track.sync_candidates()
    return track


# -- the cause, as a case ---------------------------------------------------------------------------


def test_a_trailing_id3v1_is_left_out_of_both_digests(tmp_path):
    """A file with an ID3v1 tag at the end, and the same file without one: one recording, and now
    one answer from both digests.

    It used to be two from the cheap one — BD8 in miniature, and the reason the identity decodes.
    **R-433, ruling 2 went further**: the decoded digest moved too, on a real file of the user's,
    because the demuxer hands that 128-byte block to the *decoder* as audio. Neither digest sees it
    any more, so both answer about the recording.
    """
    from mutagen.id3 import ID3

    original = encode(tmp_path / "with-id3v1.mp3", title="One", artist="A Band")
    with original.open("ab") as out:                      # 128 bytes of ID3v1, as a tagger of 2003 left it
        out.write(b"TAG" + b"One".ljust(30) + b"A Band".ljust(30) + b"An Album".ljust(30)
                  + b"2003" + b"ripped by somebody".ljust(30) + b"\xff")
    copy = tmp_path / "rewritten.mp3"
    shutil.copy2(original, copy)
    ID3(copy).save(copy)                                  # mutagen rewrites the file and drops the ID3v1

    assert copy.stat().st_size != original.stat().st_size
    assert stream_sha(copy) == stream_sha(original), "the tail is not part of the stream"
    assert decoded_sha(copy) == decoded_sha(original), "nor of the recording"
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


# -- the tail, and the route an mp3 takes to ffmpeg (R-433, ruling 2) -------------------------------


def with_a_tail(path, title, pad=b" ", comment="ripped by Sir_Mc_Tod"):
    """A real 128-byte ID3v1.1 block, padded the way the collection's files are."""
    def field(text, width):
        return text.encode("latin-1")[:width].ljust(width, pad)
    with path.open("ab") as out:
        out.write(b"TAG" + field(title, 30) + field("A Band", 30) + field("An Album", 30)
                  + b"2003" + field(comment, 28) + b"\x00\x01" + b"\x0c")
    return path


def three_endings(tmp_path, one_second_of_mp3, stub=b""):
    """One mp3 stream, ending three ways: no tail, the owner's, and the one 1.31.2 wrote.

    `stub` goes between the audio and the tail: see `pathological` below for what it is for.
    """
    plain = tmp_path / "plain.mp3"
    shutil.copy(one_second_of_mp3, plain)
    raw = plain.read_bytes()
    made = []
    for name, how in (("spaced.mp3", {}), ("nulled.mp3", {"pad": b"\x00", "comment": ""})):
        path = tmp_path / name
        path.write_bytes(raw + stub)
        made.append(with_a_tail(path, "One", **how))
    return (plain, *made)


def whole(path):
    """What ffmpeg decodes when it is given the file itself — tail and all."""
    done = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(path), "-map", "0:a",
                           "-f", "s16le", "-"], capture_output=True)
    assert done.returncode == 0 and done.stdout
    return done.stdout


def test_the_digests_do_not_move_when_only_the_tail_does(tmp_path, one_second_of_mp3):
    """Both digests exist to answer "is this the same recording", and the recording is the stream.

    A digest an ordinary retag can move is a precaution that refuses correct writes — which is what
    it did, an hour into a six-batch pass. The stream here is a plain tone, on which this ffmpeg
    happens to ignore a trailing block; the case below is the one that would catch the guard being
    taken out again.
    """
    plain, spaced, nulled = three_endings(tmp_path, one_second_of_mp3)
    assert spaced.read_bytes()[:-128] == nulled.read_bytes()[:-128]
    assert spaced.read_bytes() != nulled.read_bytes(), "they differ in those 128 bytes, and only there"

    assert decoded_sha(spaced) == decoded_sha(nulled) == decoded_sha(plain)
    assert stream_sha(spaced) == stream_sha(nulled) == stream_sha(plain)


@pytest.fixture
def pathological(tmp_path, one_second_of_mp3):
    """Two files of the shape `Fan The Fire` really has: the last frame runs into the tail.

    Measured on the user's own file, 10,123,878 bytes: change nothing but its last 128 bytes and the
    decoded PCM differs from 23.186 ms before the end — one mp3 frame — while staying the same
    length. The reason is that its final frame header declares more bytes than there are before the
    block, so the decoder reads the block as audio. A freshly encoded tone does not do this, which
    is why the shape is built here: the audio is followed by a frame header and a few bytes, so that
    frame is short and the tail finishes it.

    The stub's length is searched rather than fixed, because how many bytes it takes is a property
    of this ffmpeg, not of the point being made.
    """
    i = pathlib.Path(one_second_of_mp3).read_bytes().index(b"\xff\xfb")
    header = pathlib.Path(one_second_of_mp3).read_bytes()[i:i + 4]
    for short_by in range(8):
        _, spaced, nulled = three_endings(tmp_path, one_second_of_mp3, header + b"\x00" * short_by)
        if whole(spaced) != whole(nulled):
            return spaced, nulled
    pytest.skip("this ffmpeg decodes no trailing block as audio: the premise cannot be set up")


def test_a_tail_the_decoder_does_read_is_still_left_out_of_both_digests(pathological):
    """The measurement that explains the stop, and the guard that would catch its return.

    With the tail counted as audio these two files are not the same recording — which is exactly
    what `safely` concluded, correctly by its own measure, about a file whose stream it had not
    changed by one byte.
    """
    spaced, nulled = pathological
    assert len(whole(spaced)) == len(whole(nulled))
    assert whole(spaced) != whole(nulled), "the fixture's premise: the decoder reads the block"

    assert decoded_sha(spaced) == decoded_sha(nulled)
    assert stream_sha(spaced) == stream_sha(nulled)


def test_an_mp3s_digest_is_the_same_whether_the_tail_is_physically_there(tmp_path, one_second_of_mp3):
    """Because the snapshot digests the file with its tail and the proof digests it without one.

    The two halves of `safely` must be the same function of the same audio, or the precaution
    refuses correct writes — and reading an mp3 from its file is **not** the same function as piping
    it in: this tone decodes to 88,200 bytes of PCM read from disk and 89,950 piped, because over a
    pipe ffmpeg applies no gapless trimming. So an mp3 is always piped, and this is the case that
    says so.
    """
    plain, spaced, _ = three_endings(tmp_path, one_second_of_mp3)
    cut = tmp_path / "cut.mp3"
    cut.write_bytes(spaced.read_bytes()[:-128])
    assert cut.read_bytes() == plain.read_bytes()

    assert decoded_sha(spaced) == decoded_sha(cut)
    assert stream_sha(spaced) == stream_sha(cut)


def test_only_an_mp3_is_piped_in(tmp_path, one_second_of_sound):
    """Every other format is read from its file: the routes agree there, and an mp4's index is at
    its end, where a pipe cannot reach back to it. Measured on the user's library: opus, flac and
    m4a give the same bytes either way, mp3 does not.
    """
    opus = tmp_path / "a.opus"
    shutil.copy(one_second_of_sound, opus)
    read = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(opus), "-map", "0:a",
                           "-f", "s16le", "-"], capture_output=True).stdout

    assert ffmpeg_audio(opus, "s16le") == read
