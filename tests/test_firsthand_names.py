"""A file's own name is the files' word too (DESIGN §9, slice 153; R-525).

R-525 states the rule: an album with no matched release keeps what it was taken in with, and a
per-track lookup does not overwrite it. Checked against the code, it did not hold, in two ways —
both found on `Depeche Mode/Policy of Truth`, which matches no release at all:

* `FIRSTHAND` held only `FILE_TAGS`, so a title read off the **file name** was fair game. That is
  how `Kaleid` became `Kaleid (remix)`: one `search_recordings` hit, and the folder's own word for
  the song was gone.
* `enrich_track`'s "MusicBrainz lacks our bracket group" branch kept the title but wrote
  `SOURCE_TITLE` over its provenance. So a `file_tags` title that survived one lookup **stopped
  being firsthand**, and the next lookup — or the next pass — could replace it after all.

What MusicBrainz said is never thrown away either way: it stays in `auto`, where the page's reset
can still reach it.
"""

from __future__ import annotations

from typing import Any

from noaap.enrich import FIRSTHAND, enrich_track
from noaap.models import PlanTrack, Provenance

CREDIT = [{"name": "Depeche Mode", "artist": {"name": "Depeche Mode"}}]


class OneRecording:
    """MusicBrainz with a single answer, under whatever name the case needs."""

    def __init__(self, title: str):
        self.title = title

    def search_recordings(self, artist: str, title: str) -> list[dict[str, Any]]:
        return [{"id": "rec-1", "title": self.title, "artist-credit": CREDIT,
                 "releases": [], "length": 290000}]

    def search_releases(self, artist, album):
        return []

    def release(self, mbid):
        return {}

    def artist(self, name):
        return None

    def artist_albums(self, artist):
        return []


def a_track(title: str, provenance: Provenance) -> PlanTrack:
    t = PlanTrack(video_id="/m/04.mp3", number=4, artist="Depeche Mode", title=title,
                  filename="04 - Kaleid.mp3", provenance={"title": provenance}, state="done")
    t.auto["title"] = title
    return t


# -- the names the files carry -----------------------------------------------------------------


def test_the_file_name_and_the_folder_name_are_firsthand():
    assert Provenance.FILE_TAGS in FIRSTHAND
    assert Provenance.FILE_NAME in FIRSTHAND
    assert Provenance.FOLDER_NAME in FIRSTHAND


def test_a_title_from_the_file_name_survives_an_unmatched_lookup():
    """`Kaleid` in the folder, `Kaleid (remix)` in MusicBrainz, no release matched: the folder wins
    and the lookup's answer waits in `auto` for anyone who asks for it."""
    t = a_track("Kaleid", Provenance.FILE_NAME)

    enrich_track(t, OneRecording("Kaleid (remix)"))

    assert t.title == "Kaleid"
    assert t.provenance["title"] == Provenance.FILE_NAME, "and it is still firsthand afterwards"
    assert t.auto["title"] == "Kaleid (remix)", "what MusicBrainz said is kept, not written"


def test_a_title_from_the_folder_name_survives_too():
    t = a_track("Kaleid", Provenance.FOLDER_NAME)
    enrich_track(t, OneRecording("Kaleid (remix)"))
    assert t.title == "Kaleid"


def test_a_tagged_title_still_survives_as_it_did():
    t = a_track("Kaleid", Provenance.FILE_TAGS)
    enrich_track(t, OneRecording("Kaleid (remix)"))
    assert t.title == "Kaleid"


def test_a_title_the_source_itself_wrote_is_not_firsthand():
    """The rule is about the files. A title read out of a video's own title is a shop's guess, and
    a recording MusicBrainz knows by name is better than that."""
    t = a_track("Kaleid", Provenance.SOURCE_TITLE)

    enrich_track(t, OneRecording("Kaleid (remix)"))

    assert t.title == "Kaleid (remix)"
    assert t.provenance["title"] == Provenance.MB


# -- keeping a title must not cost it its standing ----------------------------------------------


def test_keeping_our_bracket_group_leaves_the_provenance_alone():
    """The second hole: this branch keeps the title (MusicBrainz has no `(Single Version)` of it)
    but used to stamp `SOURCE_TITLE` on it, so one more lookup could take it."""
    t = a_track("Policy of Truth (Single Version)", Provenance.FILE_TAGS)

    enrich_track(t, OneRecording("Policy of Truth"))

    assert t.title == "Policy of Truth (Single Version)"
    assert t.provenance["title"] == Provenance.FILE_TAGS


def test_what_the_downgrade_cost_on_the_next_pass():
    """Played out over two passes, which is how it would really bite. The first lookup keeps our
    title (MusicBrainz has no `(Single Version)`); the second finds the whole name and would
    rewrite its spelling — which it may do to a shop's title and may not do to the file's own."""
    tagged = a_track("Policy of Truth (Single Version)", Provenance.FILE_TAGS)
    shop = a_track("Policy of Truth (Single Version)", Provenance.COLLECTION)

    for t in (tagged, shop):
        enrich_track(t, OneRecording("Policy of Truth"))                 # the keeping branch
        enrich_track(t, OneRecording("policy of truth (single version)"))  # the whole name, lowercase

    assert tagged.title == "Policy of Truth (Single Version)", "the file's own spelling stands"
    assert shop.title == "policy of truth (single version)", "a shop's does not"


def test_a_source_title_with_a_bracket_group_is_still_marked_as_ours():
    """And where the provenance was not firsthand, the branch still says where the title came from,
    because the title that is kept is ours and not MusicBrainz'."""
    t = a_track("Policy of Truth (Single Version)", Provenance.COLLECTION)

    enrich_track(t, OneRecording("Policy of Truth"))

    assert t.title == "Policy of Truth (Single Version)"
    assert t.provenance["title"] == Provenance.SOURCE_TITLE
