"""An album's stored release is the first candidate of every lookup (§9, slice 177; R-584, finding 12).

A deep read of unpinned albums let a search result replace the release the album already was: on the
share, `Catching Up With Depeche Mode` 1985 (`Somebody (remix)`, as the file says) for 1987 (plain
`Somebody`), `Speak & Spell` 1981 US (`New Life (remix)`, `(Schizo mix)`) for another edition, and
`Das Ich — Lava (Asche)` seated against its own release by title, with a track past the last one."""

from __future__ import annotations

from test_release_rank import Editions, a_plan, a_release

from noaap.enrich import _seat, enrich_release, strictly_better, takes

CATCHING_UP = ["Dreaming of Me", "New Life", "Somebody (remix)", "Shake the Disease", "Flexible"]
PLAIN = ["Dreaming of Me", "New Life", "Somebody", "Shake the Disease", "Flexible"]


def stored(plan, mbid, files=None):
    """The fresh read of an album whose plan says `mbid`: the plan's titles and recordings go along as
    hints, and the files' own tags — `files` — are what the fresh read has."""
    plan.known_release = mbid
    for n, t in enumerate(plan.tracks, 1):
        t.known_title, t.known_recording = t.title, f"rec-{mbid}-{n}"
        if files:
            t.title = files[n - 1]
    return plan


def test_the_stored_release_that_has_every_title_with_its_take_stays():
    old = a_release("1985", CATCHING_UP, date="1985-11-11")
    new = a_release("1987", PLAIN, date="1987-06-16")
    # the files' own tags say plain `Somebody` — as on the share — so by them alone 1987 fits better
    plan = stored(a_plan(CATCHING_UP), "1985", files=PLAIN)
    mb = Editions(new, old)
    assert enrich_release(plan, mb)
    assert plan.mbid == "1985"
    assert [t.title for t in plan.tracks] == CATCHING_UP, "no take is dropped"


def test_speak_and_spell_keeps_its_remixes():
    titles = ["New Life (remix)", "Puppets", "Any Second Now (Voices)", "Just Can't Get Enough (Schizo mix)", "Photographic"]
    other = ["New Life", "Puppets", "Any Second Now (voices)", "Just Can’t Get Enough", "Photographic"]
    plan = stored(a_plan(titles), "us81", files=["New Life", "Puppets", "Any Second Now (Voices)",
                                                  "Just Can't Get Enough", "Photographic"])
    mb = Editions(a_release("other", other, date="1981-01-01"), a_release("us81", titles, date="1981-10-06"))
    assert enrich_release(plan, mb)
    assert plan.mbid == "us81"
    assert [t.title for t in plan.tracks] == titles


def test_a_search_result_that_fits_strictly_better_replaces_it():
    """`Sabaton — Metalizer (Re-Armed)`: the stored release had 14 of the album's titles, the found one all."""
    mine = ["One", "Two", "Three", "Four", "Five"]
    plan = a_plan(mine)
    plan.known_release = "poor"
    mb = Editions(a_release("poor", ["One", "Two", "Three", "Other", "Else"]), a_release("full", mine, date="2005"))
    assert enrich_release(plan, mb)
    assert plan.mbid == "full"


def test_a_tie_keeps_the_stored_one():
    plan = stored(a_plan(), "b")
    mb = Editions(a_release("a", date="1999"), a_release("b", date="2003"))   # `a` ranks first on its own
    assert enrich_release(plan, mb)
    assert plan.mbid == "b"
    assert not strictly_better(plan, a_release("a"), a_release("b"))


def test_without_a_stored_release_the_search_decides_as_before():
    plan = a_plan()
    assert enrich_release(plan, Editions(a_release("b", date="2003"), a_release("a", date="1999")))
    assert plan.mbid == "a"


def test_the_recording_a_track_already_is_seats_it_whatever_its_file_says():
    """Lava (Asche): the files say `Schwarzer Stern` and `Schwarzer Stern (by Terminal Choice)`."""
    release = a_release("lava", ["Schwarzer Stern (remix)", "Uterus", "Meine Wiege", "Vulkan",
                                 "Schwarzer Stern (reworked by Terminal Choice)"])
    plan = a_plan(["Schwarzer Stern", "Uterus", "Meine Wiege", "Vulkan", "Schwarzer Stern (by Terminal Choice)"])
    plan.tracks[0].known_recording = "rec-lava-1"
    plan.tracks[4].known_recording = "rec-lava-5"
    flat = [{**t, "disc": 1} for t in release["media"][0]["tracks"]]
    seats = _seat(plan, flat, pinned=True)
    assert seats[0]["position"] == 1 and seats[4]["position"] == 5


def test_a_sleeve_names_its_takes():
    assert takes("Fly on the Windscreen (final)") == {"final"}
    assert takes("Any Second Now (Voices)") == {"voices"}
    assert takes("Schwarzer Stern (reworked by Terminal Choice)") == {"reworked"}
    assert takes("Fly on the Windscreen") == set()
