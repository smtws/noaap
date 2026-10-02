"""An adopted album's values are its owner's (DESIGN §9, slice 103; R-410, ruling 5).

The tidying that makes a playlist into an album is about *video titles*: a label suffix, "(Official
Video)", the artist in front of the song, the album's name repeated on every track. A folder's tags
carry none of that — somebody wrote them — and the dry run over the user's own collection showed
what reading them as if they did costs: 108 titles losing `(Live in Dresden)` on live albums, 7 left
malformed as `Lá í mbealtaine ( - Bonus Track)`, `The Metropole Orchestra` replaced by `Within
Temptation`, `2003-01-01` shortened to `2003` on 336 files.

So for an adopted album noaap trims whitespace, reads numbers its own way, sets the totals, and
fills a field that is **empty**. Everything else is left exactly as stated — unless the library asks
for the tidying anyway, which is what `tidy_adopted_tags` is for.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from test_collisions import a_service
from test_intake import QUIET

from noaap import intake, sources
from noaap.config import Config
from noaap.download import would_do


def tagged(folder, name, one_second_of_sound, **tags):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    shutil.copy(one_second_of_sound, path)
    audio = MFile(path)
    for key, value in tags.items():
        audio[key] = [str(value)]
    audio.save()
    return path


@pytest.fixture
def live_album(tmp_path, one_second_of_sound):
    """A live album as its owner tagged it: the venue in every title, a guest, a full date."""
    root = tmp_path / "collection"
    album = root / "Eisbrecher" / "Live in Dresden"
    for n, title in enumerate(["Wir sind allein (Live in Dresden)", "Winter (Live in Dresden)",
                               "Eisbrecher - Verrückt (Live in Dresden)"], 1):
        tagged(album, f"{n:02d} {n}.opus", one_second_of_sound, title=title, artist="Eisbrecher",
               albumartist="Eisbrecher", album="Live in Dresden", tracknumber=f"{n:02d}",
               date="2003-01-01")
    return root, album


def a_plan(root, album, *, tidy=False):
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    if tidy:
        source.tidy_entries = True       # what `tidy_adopted_tags` sets when the library asks
    return source, intake._adopted(album, root, source, log=lambda s: None)


def test_the_venue_stays_in_the_title(live_album):
    root, album = live_album
    _, plan = a_plan(root, album)
    assert [t.title for t in plan.tracks] == ["Wir sind allein (Live in Dresden)",
                                              "Winter (Live in Dresden)",
                                              "Eisbrecher - Verrückt (Live in Dresden)"]


def test_the_library_can_ask_for_the_tidying_anyway(live_album):
    root, album = live_album
    _, plan = a_plan(root, album, tidy=True)
    assert [t.title for t in plan.tracks] == ["Wir sind allein", "Winter", "Verrückt"], \
        "with tidy_adopted_tags on, an adopted album is read as a fetched one is"


def test_a_track_artist_is_not_replaced_by_the_album_s(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = root / "Within Temptation" / "Black Symphony"
    tagged(album, "01 one.opus", one_second_of_sound, title="Jillian", artist="Within Temptation",
           albumartist="Within Temptation", album="Black Symphony", tracknumber="01")
    tagged(album, "02 two.opus", one_second_of_sound, title="Intro", artist="The Metropole Orchestra",
           albumartist="Within Temptation", album="Black Symphony", tracknumber="02")

    _, plan = a_plan(root, album)
    assert [t.artist for t in plan.tracks] == ["Within Temptation", "The Metropole Orchestra"]


def test_an_empty_field_is_still_filled(tmp_path, one_second_of_sound):
    """What is not written down is derived; what is written down stands."""
    root = tmp_path / "collection"
    album = root / "Eisregen" / "Farbenfinsternis"
    tagged(album, "Eisregen - Farbenfinsternis - 01 - Dein Blut.opus", one_second_of_sound,
           album="Farbenfinsternis", tracknumber="01")

    _, plan = a_plan(root, album)
    assert plan.tracks[0].title == "Dein Blut"
    assert plan.tracks[0].artist == "Eisregen"


def test_the_file_keeps_its_own_date(live_album):
    root, album = live_album
    source, plan = a_plan(root, album)
    said = would_do(plan, album, library=root, want=QUIET.as_treatment())
    assert not [line for line in said if "date" in line], said


def test_the_date_still_arrives_where_the_file_has_none(tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = root / "Eisregen" / "Farbenfinsternis"
    tagged(album, "01 one.opus", one_second_of_sound, title="Dein Blut", artist="Eisregen",
           albumartist="Eisregen", album="Farbenfinsternis", tracknumber="01", date="2001")
    tagged(album, "02 two.opus", one_second_of_sound, title="Kirche", artist="Eisregen",
           albumartist="Eisregen", album="Farbenfinsternis", tracknumber="02")

    _, plan = a_plan(root, album)
    said = would_do(plan, album, library=root, want=QUIET.as_treatment())
    assert [line for line in said if "date nothing → “2001”" in line], said


def test_a_repair_does_not_tidy_an_adopted_album_either(live_album):
    """The take-in leaves the values alone; the next repair used to put the tidying back."""
    root, album = live_album
    service = a_service(root)
    intake.take_in(service, root, QUIET, dry_run=False, log=lambda s: None)
    service.repair(dry_run=False, apply=True)

    from noaap.download import load_plan
    plan = load_plan(album)
    assert [t.title for t in plan.tracks] == ["Wir sind allein (Live in Dresden)",
                                              "Winter (Live in Dresden)",
                                              "Eisbrecher - Verrückt (Live in Dresden)"]
