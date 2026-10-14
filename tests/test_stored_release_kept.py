"""A stored release that still exists and fits is kept (§9, slice 198; q23, R-666).

The deep check of 2026-10-13 (r660) moved two unchanged folders to another release:
* `Mono Inc. — Symphonic Live`: the stored digital release titles every track `… (Symphonic Live)`,
  the folder's files say the plain title and are cut elsewhere than MusicBrainz cuts them (424 s
  against 282 s). q16's length gate let only 7 of 18 stored recordings hold their release, so it
  fitted not at all and the CD release (plain titles, 7 + 11) won. 1.67.0 kept it.
* `Feuerschwanz — Knightclub`: two releases with the same 12 recordings; the stored one has 4
  lengths, a later one all 12, and the closer length average replaced the stored release.
"""

from __future__ import annotations

from test_release_rank import Editions, a_plan

from noaap.enrich import enrich_release, strictly_better

CREDIT = [{"name": "A Band", "artist": {"name": "A Band", "id": "a"}}]
SONGS = ["Welcome to Hell", "Kein Weg zu weit", "Long Live Death", "If I Fail", "Time to Go", "Risk It All"]


def a_release(mbid, media, date="2019-05-24", recs=None, title="An Album"):
    """`media`: one list per disc of (title, length in s or None)."""
    n = 0
    out = []
    for d, tracks in enumerate(media, 1):
        disc = []
        for p, (name, length) in enumerate(tracks, 1):
            n += 1
            one = {"position": p, "title": name, "artist-credit": CREDIT,
                   "recording": {"id": (recs or {}).get(n, f"rec-{mbid}-{n}")}}
            if length:
                one["length"] = int(length * 1000)
            disc.append(one)
        out.append({"position": d, "tracks": disc})
    return {"id": mbid, "title": title, "date": date, "country": "XW", "status": "Official",
            "artist-credit": CREDIT, "release-group": {"id": "rg", "first-release-date": date},
            "media": out}


def stored_plan(mbid, titles, lengths, recs):
    plan = a_plan(titles)
    plan.known_release = mbid
    for t, length, rec in zip(plan.tracks, lengths, recs, strict=True):
        t.file_length = length
        t.known_recording, t.known_seat = rec, (t.disc, t.number)
    return plan


def test_symphonic_live_keeps_its_release_and_every_seat():
    mb_lengths = [244, 282, 263, 361, 237, 278]
    files = [249.6, 424.4, 262.0, 349.1, 416.8, 329.0]          # 4 of 6 off by more than 5 s
    # the ids sort the CD first, so a tie of the ranks alone would take it (R-668)
    live = a_release("digital", [[(f"{s} (Symphonic Live)", n) for s, n in zip(SONGS, mb_lengths)]],
                     title="Symphonic Live")
    cd = a_release("cd", [[(s, n + 1) for s, n in zip(SONGS[:3], mb_lengths[:3])],
                          [(s, n + 1) for s, n in zip(SONGS[3:], mb_lengths[3:])]], title="Symphonic Live")
    plan = stored_plan("digital", SONGS, files, [f"rec-digital-{n}" for n in range(1, 7)])

    assert enrich_release(plan, Editions(cd, live))

    assert plan.mbid == "digital"
    assert [(t.disc, t.number) for t in plan.tracks] == [(1, n) for n in range(1, 7)], "no seat moves"
    assert [t.mbid for t in plan.tracks] == [f"rec-digital-{n}" for n in range(1, 7)]


def test_knightclub_keeps_its_release_when_the_other_only_has_more_lengths():
    recs = {n: f"rec-{n}" for n in range(1, 7)}
    files = [238.9, 218.6, 227.4, 227.6, 215.0, 106.4]
    thin = a_release("xw", [[(s, n) for s, n in zip(SONGS, [201.8, 213.5, 214.1, None, None, None])]], recs=recs)
    full = a_release("us", [[(s, n) for s, n in zip(SONGS, [201.8, 213.5, 214.1, 228, 215, 106])]],
                     date="2025", recs=recs)
    # as on the share, a credit in the files' titles: 4 of 6 titles are the release's (9 of 12 there)
    plan = stored_plan("xw", [f"{SONGS[0]} feat. Dag", f"{SONGS[1]} feat. Doro", *SONGS[2:]], files,
                       list(recs.values()))

    assert not strictly_better(plan, full, thin), "the same titles and recordings: lengths alone decide nothing"
    assert enrich_release(plan, Editions(full, thin))
    assert plan.mbid == "xw"


def test_a_release_with_more_of_the_titles_still_replaces_the_stored_one():
    """Slice 177's rule stands: strictly more same-take titles win."""
    plan = stored_plan("poor", SONGS, [200.0] * 6, [f"rec-poor-{n}" for n in range(1, 7)])
    poor = a_release("poor", [[(s, 200) for s in SONGS[:3]] + [("Other", 200), ("Else", 200), ("More", 200)]])
    full = a_release("full", [[(s, 200) for s in SONGS]], date="2005")
    for t in plan.tracks[3:]:
        t.known_recording = None
    assert enrich_release(plan, Editions(poor, full))
    assert plan.mbid == "full"
