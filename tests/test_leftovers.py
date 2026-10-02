"""Nothing with audio in it is passed over in silence (DESIGN §9, slice 103; R-410, ruling 2).

Measured on the user's own collection: 1,318 folders hold audio and 1,310 were planned for. The
other eight — 127 files — were simply absent from the dry run, and the only way to find that out was
to count the tree against the plan by hand. Four of the eight were defects in what counts as an
album (a name beginning with an ellipsis, discs spelled `1-3`, an empty disc beside two full ones);
the rest are the owner's own arrangements, and those are named rather than guessed at.
"""

from __future__ import annotations

import shutil

import pytest
from mutagen import File as MFile
from test_collisions import a_service, an_album
from test_intake import QUIET

from noaap import intake, sources, staged
from noaap.config import Config


def audio(folder, name, *, title, album, artist, one_second_of_sound, number=1):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    shutil.copy(one_second_of_sound, path)
    tags = MFile(path)
    tags["title"], tags["album"] = [title], [album]
    tags["artist"] = tags["albumartist"] = [artist]
    tags["tracknumber"] = [str(number)]
    tags.save()
    return path


@pytest.fixture
def corners(tmp_path, one_second_of_sound):
    """One collection holding every corner the user's own has."""
    root = tmp_path / "collection"
    sound = one_second_of_sound
    # an album whose name begins with an ellipsis — a name, not a hidden folder
    an_album(root / "Crematory" / "...Just Dreaming", ["Heaven's Throat", "Transmigration"],
             album="...Just Dreaming", artist="Crematory", one_second_of_sound=sound)
    # three discs spelled "1 of 3", "2 of 3", "3 of 3"
    for disc in (1, 2, 3):
        audio(root / "Samsas Traum" / "Vernunft ist Nichts" / f"{disc}-3", f"0{disc} Teil.opus",
              title=f"Teil {disc}", album="Vernunft ist Nichts", artist="Samsas Traum",
              one_second_of_sound=sound, number=disc)
    # a box whose first disc folder is empty
    (root / "Metallica" / "Live Shit" / "1").mkdir(parents=True)
    for disc in (2, 3):
        audio(root / "Metallica" / "Live Shit" / str(disc), f"0{disc} Through The Never.opus",
              title=f"Live {disc}", album="Live Shit", artist="Metallica",
              one_second_of_sound=sound, number=disc)
    # mp3s in a hidden folder, an album one level too deep, and a folder of singles
    audio(root / "Schandmaul" / ".thumb", "01 Knüppel.opus", title="Knüppel", album="Leif",
          artist="Schandmaul", one_second_of_sound=sound)
    audio(root / "Limp Bizkit" / "Limp Bizkit" / "Significant Other", "11 Trust.opus",
          title="Trust?", album="Significant Other", artist="Limp Bizkit", one_second_of_sound=sound)
    loose = root / "Spotify"
    for n, (title, album) in enumerate([("Der Teufel", "Leif"), ("Chasing Cars", "Eyes Open")], 1):
        audio(loose, f"1-0{n}. {title}.opus", title=title, album=album, artist="Various",
              one_second_of_sound=sound, number=n)
    audio(loose / "Vol. 20", "01 Glasses.opus", title="Glasses", album="Vol. 20",
          artist="My Dark Lullabies", one_second_of_sound=sound)
    return root


def said_by_a_dry_run(root):
    lines = []
    intake.take_in(a_service(root), root, QUIET, dry_run=True, log=lines.append)
    return lines


def test_an_album_whose_name_begins_with_an_ellipsis_is_taken_in(corners):
    done = intake.take_in(a_service(corners), corners, QUIET, dry_run=True, log=lambda s: None)
    albums = [line for line in done.would if "— ...Just Dreaming (" in line]
    assert len(albums) == 1, done.would
    assert "2 track(s)" in albums[0]


def test_discs_spelled_one_of_three_are_discs(corners):
    source = sources.get("folder", Config(library_root=corners))
    source.digests = False
    album = corners / "Samsas Traum" / "Vernunft ist Nichts"
    plan = intake._adopted(album, corners, source, log=lambda s: None)
    assert plan is not None, "the album is found at all"
    assert sorted(t.disc for t in plan.tracks) == [1, 2, 3]


def test_an_empty_first_disc_does_not_cost_the_album(corners):
    source = sources.get("folder", Config(library_root=corners))
    source.digests = False
    plan = intake._adopted(corners / "Metallica" / "Live Shit", corners, source, log=lambda s: None)
    assert plan is not None
    assert len(plan.tracks) == 2


def test_what_is_not_taken_in_is_named_with_its_reason_and_count(corners):
    lines = said_by_a_dry_run(corners)
    section = [line for line in lines if line.startswith("not taken in")]
    assert len(section) == 1, lines
    body = "\n".join(lines[lines.index(section[0]) + 1:])

    assert "Schandmaul/.thumb" in body and "hidden" in body
    assert "Limp Bizkit/Limp Bizkit/Significant Other" in body and "one level too deep" in body
    assert "Spotify/Vol. 20" in body
    # the folder of singles says both facts: why it is refused, and that it holds folders of its own
    spotify = [line for line in body.splitlines() if line.strip().startswith("Spotify —")]
    assert len(spotify) == 1, body
    assert "different albums by their own tags" in spotify[0]
    assert "it also holds 1 folder(s) of its own" in spotify[0]
    # and the counts are the files, not the folders
    assert "2 file(s)" in spotify[0]


def test_a_collection_with_nothing_left_over_says_nothing(tmp_path, one_second_of_sound):
    root = tmp_path / "tidy"
    an_album(root / "Aphelion" / "Nocturnes", ["First", "Second"], album="Nocturnes",
             artist="Aphelion", one_second_of_sound=one_second_of_sound)
    lines = said_by_a_dry_run(root)
    assert not [line for line in lines if line.startswith("not taken in")]


def test_the_staged_dry_run_says_it_too(corners, tmp_path):
    lines = []
    staged.take_in_staged(a_service(corners), corners, QUIET, staging=tmp_path / "staging",
                          dry_run=True, log=lines.append)
    section = [line for line in lines if line.startswith("not taken in")]
    assert len(section) == 1, lines
    assert any("Schandmaul/.thumb" in line for line in lines)
