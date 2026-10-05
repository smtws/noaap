"""A folder's numbers are kept as stated, duplicates and all (R-423, point 1).

The user's Crematory `Early Years` holds nineteen files whose tags and names agree and in which every
number from 1 to 9 appears twice: two discs somebody flattened into one folder. The rule written for
a *fetched* album — of two entries stating one number the first keeps it and the second counts as
unnumbered — then renumbered the second run 10-18, writing a position into eighteen of the owner's
files that nothing had ever said.

Their words: prompting while working is the last choice, a report afterwards is the first, and
nothing is ever dropped. So a folder keeps what it states, and what looks odd is reported.
"""

from __future__ import annotations

import pytest
from test_collisions import an_album
from test_intake import QUIET

from noaap import intake, sources
from noaap.config import Config
from noaap.models import Entry
from noaap.plan import clashing_names, stated_numbers, wanted_filename

NO_TOTAL = " · no track total is written until this is answered"


def entries(numbers, discs=None):
    return [Entry(video_id=str(i), position=i, title=f"t{i}", number=n,
                  disc=(discs[i] if discs else 1))
            for i, n in enumerate(numbers)]


def test_a_folder_keeps_every_number_it_states():
    assert stated_numbers(entries([1, 2, 3, 1, 2, 3]), keep_duplicates=True) == [1, 2, 3, 1, 2, 3]


def test_a_fetched_album_still_counts_the_second_one_on():
    """The source there cannot know a number, so two of one is a position to fill (R-373)."""
    assert stated_numbers(entries([1, 2, 3, 1, 2, 3])) == [1, 2, 3, 4, 5, 6]


def test_a_gap_is_still_a_gap_either_way():
    assert stated_numbers(entries([1, 2, 4]), keep_duplicates=True) == [1, 2, 4]
    assert stated_numbers(entries([1, 2, 4])) == [1, 2, 4]


def test_a_file_with_no_number_still_gets_one():
    assert stated_numbers(entries([1, None, 1]), keep_duplicates=True) == [1, 2, 1]


@pytest.fixture
def flattened(tmp_path, one_second_of_sound):
    """One folder, two runs of 1-3, as the owner left it."""
    root = tmp_path / "collection"
    album = root / "Crematory" / "Early Years"
    an_album(album, ["Dreams", "Ist es wahr", "Through my Soul"], album="Early Years",
             artist="Crematory", numbers=[1, 2, 3], one_second_of_sound=one_second_of_sound)
    an_album(album, ["Tears of Time", "Shadows of Mine", "Medley"], album="Early Years",
             artist="Crematory", numbers=[1, 2, 3], one_second_of_sound=one_second_of_sound)
    return root, album


def a_plan(root, album):
    source = sources.get("folder", Config(library_root=root))
    source.digests = False
    return intake._adopted(album, root, source, log=lambda s: None)


def test_the_album_keeps_both_runs(flattened):
    root, album = flattened
    plan = a_plan(root, album)

    assert sorted(t.number for t in plan.tracks) == [1, 1, 2, 2, 3, 3]
    assert len(plan.tracks) == 6, "and nothing is dropped"


def test_the_names_keep_them_apart(flattened):
    root, album = flattened
    plan = a_plan(root, album)

    names = sorted(wanted_filename(plan, t) for t in plan.tracks)
    assert len(set(names)) == 6, names
    assert names[0].startswith("Crematory - Early Years - 01 - ")
    assert not clashing_names(plan), "two numbers the same is not two names the same"


def test_a_pass_renumbers_none_of_them(flattened):
    """The scheme may rename these files — their own names are not its — but the number in each
    name is the one the file states, and two files keep the same one."""
    root, album = flattened

    intake.take_in(a_service_for(root), root, QUIET, dry_run=False, log=lambda s: None)

    names = sorted(p.name for p in album.glob("*.opus"))
    assert len(names) == 6, names
    numbers = sorted(name.split(" - ")[2] for name in names)
    assert numbers == ["01", "01", "02", "02", "03", "03"], names


def a_service_for(root):
    from noaap.service import Service
    return Service(Config(library_root=root, musicbrainz=False, lyrics=False), root,
                   log=lambda s: None)


# -- what the pass says about it (R-423, point 2; R-424) --------------------------------------


def a_plan_of(numbers, titles, tmp_path, one_second_of_sound):
    root = tmp_path / "collection"
    album = root / "A Band" / "An Album"
    an_album(album, titles, album="An Album", artist="A Band", numbers=numbers,
             one_second_of_sound=one_second_of_sound)
    return root, album, a_plan(root, album)


def test_two_whole_runs_read_as_discs(tmp_path, one_second_of_sound):
    from noaap.plan import says_duplicates
    _, _, plan = a_plan_of([1, 2, 3, 1, 2, 3],
                           ["One", "Two", "Three", "Four", "Five", "Six"],
                           tmp_path, one_second_of_sound)
    assert says_duplicates(plan) == "2 runs of 1–3 (6 files): discs?" + NO_TOTAL


def test_one_number_twice_is_said_as_itself(tmp_path, one_second_of_sound):
    from noaap.plan import says_duplicates
    _, _, plan = a_plan_of([1, 2, 2, 3], ["One", "Two", "Also two", "Three"],
                           tmp_path, one_second_of_sound)
    assert says_duplicates(plan) == "track 2 twice" + NO_TOTAL


def test_a_tidy_album_says_nothing(tmp_path, one_second_of_sound):
    from noaap.plan import says_duplicates
    _, _, plan = a_plan_of([1, 2, 3], ["One", "Two", "Three"], tmp_path, one_second_of_sound)
    assert says_duplicates(plan) is None


def test_the_titles_hint_at_what_the_second_run_is(tmp_path, one_second_of_sound):
    """R-424: on the user's own album every second-run title carries "mix". A hint, not a decision."""
    from noaap.plan import says_duplicates
    _, _, plan = a_plan_of([1, 2, 1, 2],
                           ["Dreams", "Ewigkeit", "Dreams (Deep crowl Mix)", "Ewigkeit (Staub mix)"],
                           tmp_path, one_second_of_sound)
    said = says_duplicates(plan)
    assert said.startswith("2 runs of 1–2 (4 files): discs?")
    assert 'run 2: every title says "mix"' in said


def test_no_hint_where_the_runs_do_not_differ_that_way(tmp_path, one_second_of_sound):
    from noaap.plan import says_duplicates
    _, _, plan = a_plan_of([1, 2, 1, 2], ["Dreams", "Ewigkeit", "Tears of Time", "Medley"],
                           tmp_path, one_second_of_sound)
    assert says_duplicates(plan) == "2 runs of 1–2 (4 files): discs?" + NO_TOTAL


def test_the_section_names_the_album_and_its_files(tmp_path, one_second_of_sound):
    root, album, _ = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                               tmp_path, one_second_of_sound)
    done = intake.take_in(a_service_for(root), root, QUIET, dry_run=True, log=lambda s: None)

    section = [line for line in done.would if line.startswith("needs a look")]
    assert section == ["needs a look — 1 album(s):"], done.would
    body = done.would[done.would.index(section[0]) + 1:]
    assert body[0] == "  A Band/An Album — 2 runs of 1–2 (4 files): discs?" + NO_TOTAL
    assert len([line for line in body if line.startswith("    ")]) == 4


def test_it_is_recorded_on_the_album_for_the_page(tmp_path, one_second_of_sound):
    root, album, _ = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                               tmp_path, one_second_of_sound)
    intake.take_in(a_service_for(root), root, QUIET, dry_run=False, log=lambda s: None)

    from noaap.download import load_plan
    plan = load_plan(album)
    assert plan.adopted["needs_a_look"] == "2 runs of 1–2 (4 files): discs?" + NO_TOTAL


def test_a_staged_run_says_it_too(tmp_path, one_second_of_sound):
    from noaap import staged
    root, _, _ = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)
    said = []
    staged.take_in_staged(a_service_for(root), root, QUIET, staging=tmp_path / "staging",
                          dry_run=True, log=said.append)
    assert [line for line in said if line.startswith("needs a look")], said


# -- what MusicBrainz is asked about them (R-423, point 3) ------------------------------------


class TwoDiscs:
    """A release of two media, answered without a network — the shape of Crematory's Early Years."""

    requests = 0

    def __init__(self, media, *, title="An Album", date="1996-01-01", country="DE"):
        credit = [{"name": "A Band", "artist": {"id": "a-1", "name": "A Band"}}]
        self.body = {
            "id": "47f1cf4e-b2b3-424d-8092-2fc08d8308ad",
            "title": title, "artist-credit": credit, "date": date, "country": country,
            "release-group": {"id": "rg-1", "first-release-date": date},
            "media": [
                {"position": d, "tracks": [
                    {"position": n, "title": title, "artist-credit": credit,
                     "recording": {"id": f"rec-{d}-{n}"}}
                    for n, title in enumerate(titles, 1)]}
                for d, titles in enumerate(media, 1)],
        }

    def search_releases(self, artist, album):
        return [{**self.body, "track-count": sum(len(m["tracks"]) for m in self.body["media"]),
                 "status": "Official", "score": 100}]

    def release(self, mbid):
        return self.body

    def artist(self, name):
        return None     # this double answers about a release, not about an artist (§9, slice 138)


def test_every_file_on_one_disc_and_position_assigns_the_discs(tmp_path, one_second_of_sound):
    from noaap.enrich import discs_for_duplicates
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)

    said = discs_for_duplicates(plan, TwoDiscs([["One", "Two"], ["Three", "Four"]]))

    assert "every file matched" in said and "discs 1–2 assigned" in said
    assert "47f1cf4e-b2b3-424d-8092-2fc08d8308ad" in said and "2 medium(s) of 2/2" in said
    assert {(t.disc, t.number, t.title) for t in plan.tracks} == {
        (1, 1, "One"), (1, 2, "Two"), (2, 1, "Three"), (2, 2, "Four")}


def test_the_numbers_and_the_titles_are_still_the_owner_s(tmp_path, one_second_of_sound):
    """It assigns one thing: which disc a file is on."""
    from noaap.enrich import discs_for_duplicates
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)
    before = [(t.number, t.title, t.artist) for t in plan.tracks]

    discs_for_duplicates(plan, TwoDiscs([["One", "Two"], ["Three", "Four"]]))

    assert sorted((t.number, t.title, t.artist) for t in plan.tracks) == sorted(before)


def test_the_per_disc_totals_follow(tmp_path, one_second_of_sound):
    from noaap.enrich import discs_for_duplicates
    from noaap.tag import build_tags
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)

    discs_for_duplicates(plan, TwoDiscs([["One", "Two"], ["Three", "Four"]]))

    tags = build_tags(plan, plan.tracks[0])
    assert tags["tracktotal"] == "2" and tags["totaltracks"] == "2"
    assert tags["discnumber"] == "1" and tags["disctotal"] == "2"


def test_a_partial_match_assigns_nothing_and_says_so(tmp_path, one_second_of_sound):
    from noaap.enrich import discs_for_duplicates
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)

    said = discs_for_duplicates(plan, TwoDiscs([["One", "Two"], ["Something else", "Another"]]))

    assert "nothing assigned" in said, said
    assert {t.disc for t in plan.tracks} == {1}, "every file is where it was"


def test_no_release_at_all_is_one_line(tmp_path, one_second_of_sound):
    from noaap.enrich import discs_for_duplicates

    class Nothing:
        def search_releases(self, artist, album):
            return []

    _, _, plan = a_plan_of([1, 1], ["One", "Two"], tmp_path, one_second_of_sound)
    assert discs_for_duplicates(plan, Nothing()) == "no release matched it at MusicBrainz; nothing assigned"


def test_the_pass_asks_only_about_those_albums(tmp_path, one_second_of_sound):
    """One lookup for the album with duplicates, none for the tidy one beside it."""
    root = tmp_path / "collection"
    an_album(root / "A Band" / "Twice", ["One", "Two", "Three", "Four"], album="Twice",
             artist="A Band", numbers=[1, 2, 1, 2], one_second_of_sound=one_second_of_sound)
    an_album(root / "A Band" / "Tidy", ["Five", "Six"], album="Tidy", artist="A Band",
             numbers=[1, 2], one_second_of_sound=one_second_of_sound)
    asked = []

    class Counting(TwoDiscs):
        def search_releases(self, artist, album):
            asked.append(album)
            return super().search_releases(artist, album)

    from noaap.config import Config
    from noaap.service import Service
    service = Service(Config(library_root=root, musicbrainz=True, lyrics=False), root,
                      log=lambda s: None,
                      mb=Counting([["One", "Two"], ["Three", "Four"]], title="Twice"))
    looked = intake.Choices(musicbrainz=True, lyrics=False)

    done = intake.take_in(service, root, looked, dry_run=True, log=lambda s: None)

    assert asked == ["Twice"], asked
    # assigned, so the album leaves the "needs a look" section — and the run still says what it did
    assert [line for line in done.would
            if "discs assigned from MusicBrainz" in line and "every file matched" in line], done.would
    assert not done.needs_a_look, "nothing left to look at once the discs are known"


# -- the total nobody knows (R-425) -----------------------------------------------------------


def test_no_total_is_written_where_the_numbers_repeat(tmp_path, one_second_of_sound):
    """Eighteen files numbered 1-9 twice are not a disc of eighteen, and the count is not an answer."""
    from noaap.tag import build_tags
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)

    assert plan.disc_length(1) == 0
    tags = build_tags(plan, plan.tracks[0])
    assert "tracktotal" not in tags and "totaltracks" not in tags
    assert tags["tracknumber"] == "1"


def test_the_section_says_why_there_is_none(tmp_path, one_second_of_sound):
    from noaap.plan import says_duplicates
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)
    assert says_duplicates(plan).endswith("· no track total is written until this is answered")


def test_a_total_every_file_agrees_on_is_still_kept(tmp_path, one_second_of_sound):
    """R-346 stands: the files are the only thing that can know a disc whose tracks are not all here."""
    from noaap.tag import build_tags
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)
    for track in plan.tracks:
        track.adopted_tags = {"TRCK": [f"{track.number}/9"]}

    assert plan.disc_length(1) == 9
    assert build_tags(plan, plan.tracks[0])["tracktotal"] == "9"


def test_and_once_the_discs_are_assigned_the_totals_follow(tmp_path, one_second_of_sound):
    from noaap.enrich import discs_for_duplicates
    from noaap.tag import build_tags
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)

    discs_for_duplicates(plan, TwoDiscs([["One", "Two"], ["Three", "Four"]]))

    assert build_tags(plan, plan.tracks[0])["tracktotal"] == "2"


def test_a_release_longer_than_the_folder_is_still_asked(tmp_path, one_second_of_sound):
    """The user's flattened album holds 18 files of a release of 22; that release is the answer."""
    from noaap.enrich import discs_for_duplicates
    _, _, plan = a_plan_of([1, 2, 1, 2], ["One", "Two", "Three", "Four"],
                           tmp_path, one_second_of_sound)

    said = discs_for_duplicates(plan, TwoDiscs([["One", "Two", "Spare"], ["Three", "Four", "Extra"]]))

    assert "every file matched" in said, said
    assert {t.disc for t in plan.tracks} == {1, 2}


def test_a_title_that_does_not_match_whole_is_not_a_seat(tmp_path, one_second_of_sound):
    """Measured on the real release of `Early Years`: matching on the title with its brackets
    stripped seated `Dreams (Deep crowl Mix)` on the plain `Dreams` and the plain `Dreams` on
    `Dreams (Deep Growl mix)` — every file matched, every seat unique, and the two were swapped."""
    from noaap.enrich import discs_for_duplicates
    _, _, plan = a_plan_of([1, 1], ["Dreams", "Dreams (Deep crowl Mix)"],
                           tmp_path, one_second_of_sound)

    said = discs_for_duplicates(plan, TwoDiscs([["Dreams"], ["Dreams (Deep Growl mix)"]]))

    assert "1 of 2 file(s) matched by their whole title; nothing assigned" in said, said
    assert "Not matched: Dreams (Deep crowl Mix)" in said
    assert {t.disc for t in plan.tracks} == {1}, "and nothing was assigned"
