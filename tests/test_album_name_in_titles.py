"""What the repeated-album-name rule may take out of a title (DESIGN §9, slice 150; R-521).

Found by the full dry run of 2026-10-08, which was read before anything was applied to the user's
library. `drop_album_name` exists for the shop that writes the release into every track title
("1 - Der Kuss des Kometen (Teil 01)"), and it is judged per album so a lone title track keeps its
name. Two things defeated that judgement:

* **A single's album name *is* its song name**, so every track reads "<album> (<some version>)" and
  *all* of them are hits — the share guard cannot tell that apart from a shop's prefix. 17 titles
  across 5 albums would have been written as nothing but their version: `Policy of Truth (Single
  Version)` → `single version`, `Ai Vis Lo Lop (vocal remix)` → `vocal remix`, `Summer Wine (single
  edit)` → `single edit`.
* **A matched release's titles are MusicBrainz' own words**, and this rule was running over them.

The counter-example that keeps the rule honest is the audio play: `(Teil 01)` and `(Folge 4)`
*enumerate* recordings, so they must still be taken; `(single version)` and `(Capitol mix)` name
another cut of the one song, so they must not.
"""

from __future__ import annotations

import pytest

from noaap.models import AlbumPlan, Kind, PlanTrack, Provenance
from noaap.plan import drop_album_name
from noaap.titles import strip_album_name

# -- the 17 real titles, which must all be left alone ---------------------------------------------

DAMAGE = [
    ("Policy of Truth", "Policy of Truth (Single Version)"),
    ("Policy of Truth", "Policy of Truth (Capitol Mix)"),
    ("Policy of Truth", "Policy of Truth (Beat Box Mix)"),
    ("Policy of Truth", "Policy of Truth (Trancentral Mix)"),
    ("Ai Vis Lo Lop", "Ai Vis Lo Lop (vocal remix)"),
    ("Ai Vis Lo Lop", "Ai Vis Lo Lop (instrumental remix)"),
    ("Ai Vis Lo Lop", "Ai Vis Lo Lop (original version)"),
    ("Summer Wine", "Summer Wine (single edit)"),
    ("Summer Wine", "Summer Wine (film version)"),
    ("Summer Wine", "Summer Wine (DRP Winter remix)"),
    ("Feuer und Licht", "Feuer und Licht (album version) feat. Tanzwut"),
    ("Feuer und Licht", "Feuer und Licht (club mix) feat. Tanzwut"),
    ("Feuer und Licht", "Feuer und Licht (extended mix) feat. Tanzwut"),
    ("Feuer und Licht", "Feuer und Licht (Tanzwut remix) feat. Tanzwut"),
]


@pytest.mark.parametrize(("album", "title"), DAMAGE, ids=[t for _, t in DAMAGE])
def test_a_version_of_the_album_keeps_its_name(album, title):
    """The song is the album, so all that stripping leaves is the version — which is not a title."""
    assert strip_album_name(album, title) == title


def test_the_album_name_out_of_the_middle_leaves_one_separator():
    """`Vangelis - The City - Procession` became `Vangelis - - Procession` in the dry run."""
    assert strip_album_name("The City", "Vangelis - The City - Procession") == "Vangelis - Procession"
    assert strip_album_name("The City", "Vangelis - The City - Good to see you") \
        == "Vangelis - Good to see you"


# -- and what it must still do --------------------------------------------------------------------


@pytest.mark.parametrize(("album", "title", "want"), [
    ("Der Kuss des Kometen", "1 - Der Kuss des Kometen (Teil 01)", "Teil 01"),
    ("Der Kuss des Kometen", "7 - Der Kuss des Kometen (Teil 07)", "Teil 07"),
    ("Die Hexenmeister des Metal", "Kapitel 01: Die Hexenmeister des Metal (Folge 4)",
     "Kapitel 01 (Folge 4)"),
    ("Drachentanz (Live 2008)", "Turnier (Live 2008)", "Turnier"),
    ("Lichtjahre (Live 2007)", "Lacrimosa Theme (Live 2007)", "Lacrimosa Theme"),
    ("Live (Live 1997)", "Lacrimosa Theme - Live 1997 (Live)", "Lacrimosa Theme"),
    ("Era Metallum", "Intro Gjallarhorni (Era Metallum) feat. Sami Yli-Sirniö",
     "Intro Gjallarhorni feat. Sami Yli-Sirniö"),
])
def test_a_part_number_is_still_taken(album, title, want):
    """`(Teil 01)` tells two recordings apart; `(single version)` does not. Only the first is album
    information, and the 134 titles the rule shortened cleanly in the same run must keep working."""
    assert strip_album_name(album, title) == want


def test_a_title_that_is_only_the_album_name_is_untouched():
    """Already guarded (P51); kept here because the new guards run after that one."""
    assert strip_album_name("Drachentanz (Live 2008)", "Drachentanz (Live 2008)") \
        == "Drachentanz (Live 2008)"


def test_a_lone_title_track_keeps_its_name():
    assert strip_album_name("Carolus Rex", "Carolus Rex (Swedish version)") \
        == "Carolus Rex (Swedish version)"


# -- MusicBrainz' words are MusicBrainz' -----------------------------------------------------------


def a_plan(titles, album, provenance):
    return AlbumPlan(
        source_url=f"/music/{album}", source_id=f"/music/{album}", kind=Kind.OFFICIAL_ALBUM,
        album=album, albumartist="Someone", year=2001, cover_url=None, folder=f"Someone/{album}",
        provider="folder",
        tracks=[PlanTrack(video_id=f"/music/{n:02d}.flac", number=n, artist="Someone", title=t,
                          filename=f"{n:02d}.flac", provenance={"title": provenance},
                          state="done")
                for n, t in enumerate(titles, 1)])


def test_a_matched_releases_titles_are_left_as_musicbrainz_states_them():
    """`Lacrimosa/Live in Mexico City` matched, so every title was MusicBrainz': `Lacrimosa Theme
    (live 2014)`. The rule then stripped the album's own `(Live 2014)` out of all 22 of them."""
    titles = [f"{name} (live 2014)" for name in
              ("Lacrimosa Theme", "Ich bin der brennende Komet", "Schakal", "Alleine zu zweit")]
    plan = a_plan(titles, "Live In Mexico City (Live 2014)", Provenance.MB)

    assert drop_album_name(plan.album, plan.tracks) == 0
    assert [t.title for t in plan.tracks] == titles


def test_the_same_titles_from_the_files_are_still_shortened():
    """The one difference is where the words came from: `Lacrimosa/Lichtjahre` did not match, its
    titles are the files' own, and there the rule is right."""
    titles = [f"{name} (Live 2007)" for name in
              ("Lacrimosa Theme", "Kelch der Liebe", "Schakal", "Ich bin der brennende Komet")]
    plan = a_plan(titles, "Lichtjahre (Live 2007)", Provenance.FILE_TAGS)

    assert drop_album_name(plan.album, plan.tracks) == 4
    assert [t.title for t in plan.tracks] == ["Lacrimosa Theme", "Kelch der Liebe", "Schakal",
                                              "Ich bin der brennende Komet"]


def test_the_share_is_of_the_whole_album_not_of_the_tags_alone():
    """Three of thirteen carry the album name and ten are MusicBrainz'. Three is not four fifths of
    thirteen, so the rule stays out — the share has to be measured against the album, or skipping
    MusicBrainz' titles would make any three stragglers a majority of what is left."""
    plan = a_plan([f"Song {n}" for n in range(1, 11)], "Big Live Show", Provenance.MB)
    plan.tracks += a_plan(["Encore (Big Live Show)", "Outro (Big Live Show)",
                           "Reprise (Big Live Show)"], "Big Live Show",
                          Provenance.FILE_TAGS).tracks

    assert drop_album_name(plan.album, plan.tracks) == 0
    assert plan.tracks[10].title == "Encore (Big Live Show)"


def test_the_auto_value_follows_when_the_rule_does_fire():
    plan = a_plan([f"Teil 0{n} (Der Kuss des Kometen)" for n in range(1, 5)],
                  "Der Kuss des Kometen", Provenance.SOURCE_TITLE)
    for t in plan.tracks:
        t.auto["title"] = t.title

    assert drop_album_name(plan.album, plan.tracks) == 4
    assert [t.auto["title"] for t in plan.tracks] == [t.title for t in plan.tracks]
    assert plan.tracks[0].title == "Teil 01"
