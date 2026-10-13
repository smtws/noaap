"""`--only <path>` chooses exactly the folder at that path (§9, slice 194; q18, R-652).

Round A listed `Feuerschwanz/10 Jahre Feuerschwanz Live`, and the pass also ran the separate folder
`Feuerschwanz/10 Jahre Feuerschwanz (live)`: matched part by part through `text_key`, `Live` and
`(live)` are one name. Round B took `Mono Inc/Together till the End` and `Saltatio Mortis/Zirkus
Zeitgeist- Ohne Strom und Stecker` the same way. The text key stays for a value that names no folder.
"""

from __future__ import annotations

from noaap.intake import under_only


def a_root(tmp_path):
    root = tmp_path / "lib"
    listed = root / "Feuerschwanz" / "10 Jahre Feuerschwanz Live"
    twin = root / "Feuerschwanz" / "10 Jahre Feuerschwanz (live)"
    other = root / "Feuerschwanz" / "Memento Mori"
    for one in (listed, twin, other):
        one.mkdir(parents=True)
    return root, listed, twin, other


def test_a_path_that_names_a_folder_chooses_that_folder_only(tmp_path):
    root, listed, twin, other = a_root(tmp_path)

    assert under_only([listed, twin, other], root, ["Feuerschwanz/10 Jahre Feuerschwanz Live"]) == ([listed], [])
    assert under_only([listed, twin, other], root, ["Feuerschwanz/10 Jahre Feuerschwanz (live)"]) == ([twin], [])


def test_an_absolute_path_too(tmp_path):
    root, listed, twin, other = a_root(tmp_path)

    assert under_only([listed, twin, other], root, [str(listed)]) == ([listed], [])


def test_an_artist_folder_still_holds_all_of_its_albums(tmp_path):
    root, listed, twin, other = a_root(tmp_path)

    assert under_only([listed, twin, other], root, ["Feuerschwanz"]) == ([listed, twin, other], [])


def test_a_value_that_names_no_folder_is_matched_by_its_text_key_as_before(tmp_path):
    root, listed, twin, other = a_root(tmp_path)

    assert under_only([listed, twin, other], root, ["feuerschwanz/memento mori"]) == ([other], [])
    assert under_only([listed, twin, other], root, ["Feuerschwanz/10 Jahre Feuerschwanz LIVE"])[0] \
        == [listed, twin], "no folder of that spelling: both answer, as they did"
