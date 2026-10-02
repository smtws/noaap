"""The one state of the library, and the exceptions to it (DESIGN §9, slice 100).

The user, twice. *"we had consistent state and now we got persisted state per album that has to be
manually overridden? what if i hit update or repair after adoption, they follow system settings?"* —
and, when a per-album record was proposed anyway: *"if i do an intake with deliberately few options
checked to speed things up and decide that i now want to have the image embedded everywhere i am not
going to hop through 700 albums manually."*

So: the settings say what every album should have, a pass brings an album to them, and the only thing
kept per album is an exception the user sets by hand.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from noaap.config import Config
from noaap.treatment import (
    EXCEPTIONS,
    OPERATIONS,
    Treatment,
    exceptions_of,
    for_album,
    held_back,
    says_exceptions,
    with_exception,
)


def album(**exceptions):
    return SimpleNamespace(exceptions=exceptions or None)


def test_the_settings_are_the_state_the_library_is_in():
    want = Treatment.from_settings(Config())
    assert (want.musicbrainz, want.lyrics, want.cover_beside) == (True, True, True)
    assert (want.cover_embedded, want.lyrics_embedded) == (True, True)
    # an adopted album keeps its owner's names and tags until the library is told otherwise
    assert (want.rename_adopted, want.retag_adopted) == (False, False)
    # …and its owner's values mean what they say (R-410, ruling 5)
    assert want.tidy_adopted_tags is False
    assert want.off() == ["rename_adopted", "retag_adopted", "tidy_adopted_tags"]


def test_an_album_with_no_exception_is_the_settings():
    cfg = Config()
    assert for_album(cfg, album()) == Treatment.from_settings(cfg)
    assert held_back(cfg, album()) == []
    assert says_exceptions(album()) == ""


def test_what_an_album_was_taken_in_with_does_not_follow_it_around():
    """The heart of the user's second question: a quick take-in is not a promise about the album.

    A plan may say it was adopted, that its names were kept, that no tags were written — that is the
    record of what happened. It says nothing about what a later pass may do, so turning embedding on
    in the settings reaches every one of seven hundred albums without anybody visiting them.
    """
    quick = SimpleNamespace(exceptions=None, adopted={"at": "2026-10-01"}, keep_names=True,
                            keep_tags=True, treatment={"cover_embedded": False})
    cfg = Config()
    assert for_album(cfg, quick).cover_embedded is True, "the settings decide, not the take-in"
    assert for_album(cfg, quick).lyrics_embedded is True
    assert held_back(cfg, quick) == []


def test_an_exception_takes_something_away_and_can_never_add():
    cfg = Config()
    mine = for_album(cfg, album(cover_embedded=True))
    assert mine.cover_embedded is False
    assert mine.lyrics_embedded is True, "and nothing else is touched"
    assert held_back(cfg, album(cover_embedded=True)) == ["cover_embedded"]

    # there is no exception that turns something on: an album cannot ask for more than the library does
    cfg.cover_embedded = False
    assert for_album(cfg, album(cover_embedded=True)).cover_embedded is False
    assert for_album(cfg, album()).cover_embedded is False
    assert held_back(cfg, album(cover_embedded=True)) == [], "a setting that is off holds nothing back"


def test_the_names_and_tags_of_an_adopted_album_are_excepted_together_with_the_setting():
    cfg = Config()
    cfg.rename_adopted = True          # the library is told to rename adopted albums
    assert for_album(cfg, album()).rename_adopted is True
    assert for_album(cfg, album(names=True)).rename_adopted is False
    assert held_back(cfg, album(names=True)) == ["rename_adopted"]

    cfg.retag_adopted = True
    assert for_album(cfg, album(tags=True)).retag_adopted is False
    assert held_back(cfg, album(names=True, tags=True)) == ["rename_adopted", "retag_adopted"]


def test_an_exception_reads_as_a_sentence_and_is_set_one_at_a_time():
    one = album(names=True, cover_embedded=True)
    assert says_exceptions(one) == "keep this album's names · do not embed the cover here"

    assert with_exception(album(), "lyrics", True) == {"lyrics": True}
    assert with_exception(one, "names", False) == {"cover_embedded": True}
    assert with_exception(one, "names", True) == {"names": True, "cover_embedded": True}
    with pytest.raises(ValueError, match="no such exception"):
        with_exception(album(), "something else", True)


def test_only_the_exceptions_this_program_knows_are_read():
    odd = SimpleNamespace(exceptions={"cover_embedded": True, "made up": True, "lyrics": False})
    assert exceptions_of(odd) == {"cover_embedded": True}, "unknown keys and false ones are not exceptions"
    assert set(EXCEPTIONS) <= set(OPERATIONS) | {"names", "tags"}


def test_every_switch_is_in_the_settings_and_says_what_it_is():
    cfg = Config()
    for key in OPERATIONS:
        assert hasattr(cfg, key), f"{key} has to be a setting, because the settings are the state"
    said = Treatment.from_settings(cfg).says()
    assert "looked up at MusicBrainz" in said and "the cover in the files" in said
