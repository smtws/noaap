"""A new entry whose seat a kept file holds goes last (§9, slice 203; q28, R-686).

`Evanescence — Fallen (Deluxe Edition / Remastered 2023)`, 2026-10-14: the read skipped `Everybody's Fool`
(the album's last, 1-20), and the playlist listed `My Immortal (Strings Version)` at 15 where it had
listed another video twice. Every track behind 15 would have moved up one onto the skipped track's
seat, so none moved ("neither moves"), and the Strings Version was to be fetched as 1-15, a second
file on `Bring Me to Life (Demo)`'s number.
"""

from __future__ import annotations

import copy

from test_incremental import vol1

from noaap.plan import build_plan, merge_plans


def album_and_read(at: int):
    """The stored album, every track done; the read: its last track skipped, a new video at `at`."""
    stored = build_plan(vol1())
    for t in stored.tracks:
        t.state, t.file_length = "done", 200.0
    coll = vol1()
    tracks = [e for e in coll.entries if e.video_id in {t.video_id for t in stored.tracks}]
    tracks[-1].skipped = "Sign in to confirm your age"
    new = copy.deepcopy(tracks[at - 1])
    new.video_id = "STRINGS0001"         # another video of the 5th song: the read titles it the same
    coll.entries.insert(coll.entries.index(tracks[at - 1]), new)
    for n, e in enumerate(coll.entries, 1):
        e.position = n
    return stored, merge_plans(stored, build_plan(coll))


def test_the_new_entry_goes_last_and_no_two_tracks_share_a_number():
    stored, merged = album_and_read(at=5)
    seats = [(t.disc, t.number) for t in merged.tracks]
    assert len(seats) == len(set(seats)), sorted(seats)
    new = next(t for t in merged.tracks if t.video_id == "STRINGS0001")
    assert (new.disc, new.number) == (1, len(stored.tracks) + 1)
    for t in stored.tracks:          # every file the album holds stays where it is
        assert next((m.disc, m.number) for m in merged.tracks if m.video_id == t.video_id) == (t.disc, t.number)
    held = next(t for t in stored.tracks if t.number == 5)
    assert f"1-05 {new.title}: its seat is 1-05 {held.title}'s — goes last as " \
           f"1-{len(stored.tracks) + 1:02d}" in merged.seat_notes, merged.seat_notes


def test_a_new_entry_on_a_free_seat_is_not_moved():
    """Nothing skipped: the album simply follows the playlist, as before."""
    stored = build_plan(vol1())
    for t in stored.tracks:
        t.state = "done"
    coll = vol1()
    new = copy.deepcopy(coll.entries[-1])
    new.video_id, new.title = "APPENDED001", "Bonus"
    coll.entries.append(new)
    merged = merge_plans(stored, build_plan(coll))
    assert not [n for n in merged.seat_notes if "goes last as" in n]
    seats = [(t.disc, t.number) for t in merged.tracks]
    assert len(seats) == len(set(seats))
