"""Which names a take-in gives the files, and who decided (DESIGN §9, slice 103; R-410, ruling 3).

`--names` defaulted to `scheme` on the command line while the setting it mirrors,
`rename_adopted`, defaults to off — and the settings page says "off by default". A plain
`noaap take-in` therefore renamed 2,847 files of the user's collection and moved 163 album folders,
with nothing in the run saying that a choice had been made at all.
"""

from __future__ import annotations

import pytest
from test_collisions import an_album
from test_intake import QUIET

from noaap import cli, intake
from noaap.config import Config
from noaap.service import Service


@pytest.fixture
def collection_and_config(tmp_path, one_second_of_sound, monkeypatch):
    """A small collection, and a config directory of this test's own."""
    root = tmp_path / "collection"
    an_album(root / "Aphelion" / "Nocturnes", ["First", "Second"], album="Nocturnes",
             artist="Aphelion", one_second_of_sound=one_second_of_sound)
    home = tmp_path / "config"
    (home / "noaap").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return root, home / "noaap" / "config.toml"


def test_without_the_flag_the_setting_decides_and_it_is_keep(collection_and_config, capsys):
    root, config = collection_and_config
    config.write_text(f'library_root = "{root}"\nmusicbrainz = false\nlyrics = false\n')

    cli.main(["take-in", str(root), "--no-mb", "--no-lyrics"])

    out = capsys.readouterr().out
    assert "their own names (from the rename_adopted setting)" in out, out
    assert "0 file(s) would be renamed" in out


def test_the_setting_turned_on_renames(collection_and_config, capsys):
    root, config = collection_and_config
    config.write_text(f'library_root = "{root}"\nmusicbrainz = false\nlyrics = false\n'
                      "rename_adopted = true\n")

    cli.main(["take-in", str(root), "--no-mb", "--no-lyrics"])

    out = capsys.readouterr().out
    assert "noaap's names (from the rename_adopted setting)" in out, out
    assert "01 would be renamed" in out


def test_the_flag_overrides_the_setting_and_says_so(collection_and_config, capsys):
    root, config = collection_and_config
    config.write_text(f'library_root = "{root}"\nmusicbrainz = false\nlyrics = false\n')

    cli.main(["take-in", str(root), "--names", "scheme", "--no-mb", "--no-lyrics"])

    out = capsys.readouterr().out
    assert "noaap's names (from --names)" in out, out
    assert "01 would be renamed" in out


def test_the_page_asks_the_setting_too(collection_and_config):
    root, config = collection_and_config
    service = Service(Config(library_root=root, musicbrainz=False, lyrics=False), root,
                      log=lambda s: None)
    said = []
    service.log = said.append
    service.take_in_all(root, dry_run=True, musicbrainz=False, lyrics=False)
    assert any("their own names (from the rename_adopted setting)" in line for line in said), said


def test_a_staged_dry_run_opens_with_the_same_line(collection_and_config, tmp_path):
    from noaap import staged
    root, _ = collection_and_config
    service = Service(Config(library_root=root, musicbrainz=False, lyrics=False), root,
                      log=lambda s: None)
    said = []
    staged.take_in_staged(service, root, intake.Choices(names="keep", names_from="the rename_adopted setting",
                                                        musicbrainz=False, lyrics=False),
                          staging=tmp_path / "staging", dry_run=True, log=said.append)
    assert any("their own names (from the rename_adopted setting)" in line for line in said), said
