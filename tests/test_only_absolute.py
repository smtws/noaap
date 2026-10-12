"""`--only` takes an absolute path under the library as well as a relative one (q15, R-638).

`update --only /home/…/po-p138/lib/Apocalyptica/Apocalyptica` with that `lib` as the library said
"nothing under … — no album of the library is there": the value lost its leading `/` and was matched
as `home/tordt/…` relative to the root. The same rule serves adopt, merge, take-in, repair and update.
"""

from __future__ import annotations

from noaap.intake import says_only, under_only


def a_root(tmp_path):
    root = tmp_path / "lib"
    album = root / "Apocalyptica" / "Apocalyptica"
    album.mkdir(parents=True)
    (root / "Other" / "Album").mkdir(parents=True)
    return root, album, [album, root / "Other" / "Album"]


def test_an_absolute_path_under_the_root_chooses_its_album(tmp_path):
    root, album, albums = a_root(tmp_path)

    chosen, missed = under_only(albums, root, [str(album)])

    assert chosen == [album] and missed == []


def test_a_relative_path_still_does(tmp_path):
    root, album, albums = a_root(tmp_path)

    assert under_only(albums, root, ["Apocalyptica/Apocalyptica"]) == ([album], [])
    assert under_only(albums, root, ["apocalyptica"]) == ([album], []), "matched through text_key, as before"


def test_an_absolute_path_through_a_link_to_the_root(tmp_path):
    """The root as the config spells it and the path as the shell resolved it, or the other way."""
    root, album, albums = a_root(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(root)

    assert under_only(albums, root, [str(link / "Apocalyptica" / "Apocalyptica")]) == ([album], [])
    assert under_only([link / "Apocalyptica" / "Apocalyptica"], link, [str(album)])[1] == []


def test_an_absolute_path_outside_the_root_chooses_nothing(tmp_path):
    root, album, albums = a_root(tmp_path)
    elsewhere = tmp_path / "elsewhere" / "Apocalyptica" / "Apocalyptica"

    assert under_only(albums, root, [str(elsewhere)]) == ([], [str(elsewhere)])


def test_the_run_says_which_path_it_took(tmp_path):
    root, album, albums = a_root(tmp_path)

    said = says_only([str(album)], [album], root)

    assert said == f"only: Apocalyptica/Apocalyptica (from {album}) (1 album)", said
    assert says_only(["Apocalyptica"], [album], root) == "only: Apocalyptica (1 album)"


def test_the_update_pass_takes_it(tmp_path):
    from test_listen import a_library

    from noaap.config import Config
    from noaap.service import Service

    album_dir, plan = a_library(tmp_path)
    said: list[str] = []
    service = Service(Config(musicbrainz=False, lyrics=False), tmp_path, log=said.append)

    service._only_these([(album_dir, plan)], [str(album_dir)])

    assert said[0] == f"only: Somebody/An Album (from {album_dir}) (1 album)", said
    assert not [line for line in said if "no album of the library is there" in line], said
