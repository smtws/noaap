"""A track moves only onto a seat the release has, and only when it is that track (§9, slice 192; q16).

The library-wide deep dry of 2026-10-12 (I-481) offered 88 moves on 20 albums: 22 onto seats the
release does not have — `Das Ich — Alter Ego` put `09 Kingdott` at 16 of 15 — and 20 onto seats whose
length is not the file's: `Mono Inc. — Together Till The End` swapped five same-titled pairs between
discs 1 and 3, each file onto the other's length. R-648 ruled the rule tested here.
"""

from __future__ import annotations

from test_release_rank import CREDIT, Editions

from noaap.enrich import enrich_release
from noaap.models import AlbumPlan, Kind, PlanTrack


def a_release(mbid, media):
    """`media`: one list per disc of (title, seconds)."""
    return {"id": mbid, "title": "An Album", "date": "2001", "country": "DE", "status": "Official",
            "artist-credit": CREDIT, "release-group": {"id": "rg", "first-release-date": "2001"},
            "media": [{"position": d, "tracks": [
                {"position": n, "title": title, "artist-credit": CREDIT, "length": int(seconds * 1000),
                 "recording": {"id": f"rec-{d}-{n}", "length": int(seconds * 1000)}}
                for n, (title, seconds) in enumerate(tracks, 1)]}
                for d, tracks in enumerate(media, 1)]}


def a_plan(tracks):
    """`tracks`: (disc, number, title, file seconds, stored recording or None)."""
    plan = AlbumPlan(source_url="/m", source_id="/m", kind=Kind.OFFICIAL_ALBUM, album="An Album",
                     albumartist="A Band", year=None, cover_url=None, folder="A Band/An Album",
                     provider="folder",
                     tracks=[PlanTrack(video_id=f"/m/{d}-{n:02d}.flac", number=n, disc=d, artist="A Band",
                                       title=title, filename=f"{d}-{n:02d}.flac", provenance={},
                                       state="done", file_length=seconds)
                             for d, n, title, seconds, _ in tracks])
    plan.known_release = "r"
    for t, (d, n, _, _, rec) in zip(plan.tracks, tracks, strict=True):
        t.known_seat = (d, n)
        if rec:
            t.known_recording = rec
    return plan


def seats(plan):
    return {t.title + (f" {t.file_length:.0f}" if t.file_length else ""): (t.disc, t.number) for t in plan.tracks}


def test_a_track_nothing_on_the_release_fits_keeps_its_seat():
    """Das Ich — Alter Ego: `Kingdott` is two edits from the release's `Kindgott`, so nothing matches
    it, and it went from 09 to 16 on a release of 15."""
    titles = [f"Song {n}" for n in range(1, 16)]
    titles[8] = "Kindgott"
    release = a_release("r", [[(t, 200 + n) for n, t in enumerate(titles)]])
    plan = a_plan([(1, n, "Kingdott" if n == 9 else t, 200 + n - 1, None) for n, t in enumerate(titles, 1)])
    plan.tracks[8].known_seat = (1, 16)    # as on the share: the plan already said 16, the file 09

    assert enrich_release(plan, Editions(release))

    kingdott = next(t for t in plan.tracks if t.title == "Kingdott")
    assert (kingdott.disc, kingdott.number) == (1, 9), "not 16 of 15"
    assert max(t.number for t in plan.tracks) == 15
    assert any(line.startswith("1-09 Kingdott: keeps its seat") for line in plan.seat_notes), plan.seat_notes


def test_same_titled_pairs_are_seated_by_their_length():
    """Mono Inc. — Together Till The End: one recording stored for both `Out in the Fields`, and both
    `The Tide`; the update swapped each pair onto the other's length."""
    release = a_release("r", [[("The Banks of Eden", 235), ("Out in the Fields", 246), ("The Tide", 178)],
                              [("Potter's Field", 252)],
                              [("Together Till the End", 192), ("Out in the Fields", 216), ("The Tide", 168)]])
    plan = a_plan([(1, 1, "The Banks of Eden", 217.8, "rec-1-1"),
                   (1, 2, "Out in the Fields", 246.1, "rec-3-2"),
                   (1, 3, "The Tide", 179.0, "rec-3-3"),
                   (2, 1, "Potter's Field", 252.7, "rec-2-1"),
                   (3, 1, "Together Till the End", 192.9, "rec-3-1"),
                   (3, 2, "Out in the Fields", 216.1, "rec-3-2"),
                   (3, 3, "The Tide", 168.2, "rec-3-3")])

    assert enrich_release(plan, Editions(release))

    by_length = {round(t.file_length): t for t in plan.tracks}
    assert ((by_length[246].disc, by_length[246].number), by_length[246].mbid) == ((1, 2), "rec-1-2")
    assert ((by_length[216].disc, by_length[216].number), by_length[216].mbid) == ((3, 2), "rec-3-2")
    assert ((by_length[179].disc, by_length[179].number), by_length[179].mbid) == ((1, 3), "rec-1-3")
    assert ((by_length[168].disc, by_length[168].number), by_length[168].mbid) == ((3, 3), "rec-3-3")


def test_a_match_whose_length_is_not_the_slot_s_is_refused():
    """Faun — Pagan: `Wainamoinen` (292 s) offered 1-04 (242 s); a title the release holds twice is
    no evidence on its own."""
    release = a_release("r", [[("One", 200), ("Two", 210), ("Three", 220), ("Wainamoinen", 242)]])
    plan = a_plan([(1, 1, "One", 200, None), (1, 2, "Two", 210, None), (1, 3, "Three", 220, None),
                   (1, 6, "Wainamoinen", 291.7, None)])
    release["media"][0]["tracks"].append({**release["media"][0]["tracks"][3], "position": 5,
                                          "recording": {"id": "rec-1-5", "length": 242000}})

    assert enrich_release(plan, Editions(release))

    waina = next(t for t in plan.tracks if t.title == "Wainamoinen")
    assert (waina.disc, waina.number) == (1, 6)
    assert not waina.mbid, "not that recording, so nothing of it is written"
    assert any("1-06 Wainamoinen: keeps its seat — the release's 1-04" in line for line in plan.seat_notes), \
        plan.seat_notes


def test_a_live_album_moves_by_its_whole_unique_titles():
    """Depeche Mode — 101: disc 2's files numbered 01-02 belong at 03-04 of the 20-track release; live,
    so 9 s off its lengths."""
    release = a_release("r", [[("Pimpf", 60), ("Behind the Wheel", 360), ("Black Celebration", 285.5),
                               ("Everything Counts", 391.7)]])
    plan = a_plan([(1, 1, "Pimpf", 61, None), (1, 2, "Behind the Wheel", 361, None),
                   (2, 1, "Black Celebration", 294.3, None), (2, 2, "Everything Counts", 380.0, None)])

    assert enrich_release(plan, Editions(release))

    assert seats(plan)["Black Celebration 294"] == (1, 3)
    assert seats(plan)["Everything Counts 380"] == (1, 4)
    assert not getattr(plan, "seat_notes", [])


def test_a_move_onto_a_seat_somebody_keeps_moves_neither():
    """R-648 2 b): `Bonus` keeps 1-03, which the release gives `Three`, now at 1-07."""
    release = a_release("r", [[("One", 200), ("Two", 210), ("Three", 220), ("Four", 230), ("Five", 240)]])
    plan = a_plan([(1, 1, "One", 200, None), (1, 2, "Two", 210, None), (1, 3, "Bonus", 300, None),
                   (1, 4, "Four", 230, None), (1, 5, "Five", 240, None), (1, 7, "Three", 220.4, None)])

    assert enrich_release(plan, Editions(release))

    assert seats(plan)["Bonus 300"] == (1, 3)
    assert seats(plan)["Three 220"] == (1, 7)
    assert any(line.startswith("1-03 Bonus and 1-07 Three: neither moves") for line in plan.seat_notes), \
        plan.seat_notes


def test_a_fresh_playlist_has_no_seat_to_keep():
    """Taken in for the first time: a track nothing matches is counted on after the release, as before."""
    release = a_release("r", [[("One", 200), ("Two", 210), ("Three", 220), ("Four", 230), ("Five", 240)]])
    plan = a_plan([(1, 1, "Bonus", 300, None), (1, 2, "One", 200, None), (1, 3, "Two", 210, None),
                   (1, 4, "Three", 220, None), (1, 5, "Four", 230, None), (1, 6, "Five", 240, None)])
    plan.provider = "youtube"
    for t in plan.tracks:
        del t.known_seat
    plan.known_release = None

    assert enrich_release(plan, Editions(release))

    assert [(t.title, t.number) for t in plan.tracks] == [("One", 1), ("Two", 2), ("Three", 3), ("Four", 4),
                                                         ("Five", 5), ("Bonus", 6)]


def test_a_listed_video_the_read_skips_keeps_its_seat():
    """Feuerschwanz — Walhalligalli: the playlist still lists `Metnotstand im Märchenland`, the read
    skipped it, and the update moved it from 04 to 15 of 14 while the rest moved up one."""
    from test_incremental import vol1

    from noaap.plan import build_plan, merge_plans

    existing = build_plan(vol1())
    fresh_coll = vol1()
    fourth = existing.tracks[3]
    next(e for e in fresh_coll.entries if e.video_id == fourth.video_id).skipped = "uploaded by another channel"
    merged = merge_plans(existing, build_plan(fresh_coll))

    assert [t.number for t in sorted(merged.tracks, key=lambda t: t.number)] == list(range(1, 14))
    assert [t.number for t in merged.tracks if t.video_id == fourth.video_id] == [4]
    assert {t.video_id: t.number for t in merged.tracks} == {t.video_id: t.number for t in existing.tracks}, \
        "and nobody moves onto its seat"
    assert merged.seat_notes[0].startswith(f"1-04 {fourth.title}: keeps its seat"), merged.seat_notes
