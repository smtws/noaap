"""A title the playlist now gives without its bracket is the same slot when the length says so
(§9, slice 202; q25, R-682).

`Evanescence — Fallen (Deluxe Edition / Remastered 2023)` held `01 Going Under (Remastered 2023)`
(215.2 s) and `11 My Immortal (Band Version)` (274.2 s). On 2026-10-14 the playlist listed `Going
Under` (218 s) and `My Immortal` (270 s) instead, and the Strings Version (274 s) where it had listed
the Band Version a second time. The check offered three downloads and moved the two files last;
`Metallica`'s `Enter Sandman`, the same swap with an equal title, was one slot (slice 163).
"""

from __future__ import annotations

from test_incremental import vol1

from noaap.plan import build_plan, changes_from, merge_plans


def an_album(*held):
    """The stored plan, every track done; `held` = (index, title, file seconds)."""
    stored = build_plan(vol1())
    for n, t in enumerate(stored.tracks):
        t.state, t.file_length, t.artist, t.title = "done", 200.0, "Evanescence", f"Song {n}"
    for i, title, seconds in held:
        stored.tracks[i].title, stored.tracks[i].file_length = title, seconds
    return stored


def the_playlist_lists(stored, *swaps):
    """The merge of the fresh read: `swaps` = (index, new video id, title, video seconds)."""
    return merge_plans(stored, the_playlist_lists_fresh(stored, *swaps))


def the_playlist_lists_fresh(stored, *swaps):
    """The fresh read, the lookup's recording on every swapped entry."""
    coll = vol1()
    by_id = {e.video_id: e for e in coll.entries}
    for i, vid, title, seconds in swaps:
        e = by_id[stored.tracks[i].video_id]
        e.video_id, e.title, e.duration = vid, title, seconds
    fresh = build_plan(coll)
    for f in fresh.tracks:             # what the lookup leaves: one artist, the stored titles
        f.artist = "Evanescence"
        f.title = next((s[2] for s in swaps if s[1] == f.video_id),
                       next((t.title for t in stored.tracks if t.video_id == f.video_id), f.title))
        if any(s[1] == f.video_id for s in swaps):
            f.mbid, f.mb_length = "954b2a1f", 261.4
    return fresh


def slot(plan, vid):
    return next(t for t in plan.tracks if t.video_id == vid)


def test_the_title_without_its_bracket_and_the_file_s_length_is_one_slot():
    stored = an_album((0, "Going Under (Remastered 2023)", 215.2), (4, "My Immortal (Band Version)", 274.2))
    old = [stored.tracks[0].video_id, stored.tracks[4].video_id]
    seats = [(t.disc, t.number) for t in (stored.tracks[0], stored.tracks[4])]
    merged = the_playlist_lists(stored, (0, "CdhqVtpR2ts", "Going Under", 218.0),
                                (4, "5anLPw0Efmo", "My Immortal", 270.0))

    for vid, was, seat in zip(("CdhqVtpR2ts", "5anLPw0Efmo"), old, seats, strict=True):
        t = slot(merged, vid)
        assert (t.source_override, t.state, (t.disc, t.number)) == (was, "done", seat)
    assert len(merged.tracks) == len(stored.tracks)
    lines = changes_from(stored, merged)
    assert sum("the source lists another video for it — the file stays" in s for s in lines) == 2, lines
    assert not [s for s in lines if "new in the source" in s or " → " in s], lines
    assert not [t for t in merged.tracks if t.in_source and t.state != "done"]


def test_both_titles_bracketed_is_not_a_pair_whatever_the_length():
    """The Strings Version is not the Band Version; 274 s against 274 s says nothing."""
    stored = an_album((4, "My Immortal (Band Version)", 274.2))
    merged = the_playlist_lists(stored, (4, "zf0CwQJcFCg", "My Immortal (Strings Version)", 274.0))
    t = slot(merged, "zf0CwQJcFCg")
    assert t.source_override is None and t.state != "done"
    assert any("new in the source" in s for s in changes_from(stored, merged))


def test_a_length_that_does_not_fit_is_not_a_pair():
    stored = an_album((0, "Going Under (Remastered 2023)", 215.2))
    merged = the_playlist_lists(stored, (0, "LIVEVIDEO01", "Going Under", 261.0))
    assert slot(merged, "LIVEVIDEO01").source_override is None


def test_two_new_entries_for_one_gone_track_are_no_pair():
    stored = an_album((0, "Going Under (Remastered 2023)", 215.2), (1, "Something", 200.0))
    merged = the_playlist_lists(stored, (0, "NEWVIDEO001", "Going Under", 216.0),
                                (1, "NEWVIDEO002", "Going Under", 217.0))
    assert slot(merged, "NEWVIDEO001").source_override is None
    assert slot(merged, "NEWVIDEO002").source_override is None


def test_an_equal_title_still_needs_no_length():
    """Slice 163 unchanged: Metallica's `Enter Sandman`, its lengths not asked."""
    stored = an_album((0, "Enter Sandman", 331.0))
    old = stored.tracks[0].video_id
    merged = the_playlist_lists(stored, (0, "CD-E-LDc384", "Enter Sandman", 360.0))
    assert slot(merged, "CD-E-LDc384").source_override == old


def test_the_slot_keeps_the_file_s_title_and_recording_then_and_at_the_next_read():
    """The file is still `Going Under (Remastered 2023)`: no rename to the video's title, and not the
    recording the lookup gave the video (a live one, 261 s — q26)."""
    stored = an_album((0, "Going Under (Remastered 2023)", 215.2))
    stored.tracks[0].mbid = None
    merged = the_playlist_lists(stored, (0, "CdhqVtpR2ts", "Going Under", 218.0))
    again = merge_plans(merged, the_playlist_lists_fresh(stored, (0, "CdhqVtpR2ts", "Going Under", 218.0)))
    for plan in (merged, again):
        t = slot(plan, "CdhqVtpR2ts")
        assert (t.title, t.mbid, t.number) == ("Going Under (Remastered 2023)", None, 1)
    assert not [s for s in changes_from(merged, again)]
