"""`noaap config`: a report that writes nothing, and a setter that says what it did.

DESIGN §9, slice 67. `--library` means two different things on two commands — everywhere else it
overrides the library for that one run and writes nothing, on `config` it **sets** it. Somebody used
`config --library PATH` as a read-only report and rewrote a real installation's library root with it,
once per run, for a day. So the setter names the file and prints what changed, and the report half is
held to writing nothing at all.
"""
from __future__ import annotations

import hashlib

from noaap import config
from noaap.cli import main

# -- a setter says so, a report writes nothing (§9, slice 67, R-229) --------------------------------


def test_config_without_a_setter_writes_nothing(tmp_path, monkeypatch, capsys):
    """`--library` overrides for one run on every other command and **sets** on this one. Somebody used
    `config --library PATH` as a report and rewrote a real installation's library root with it, once per
    run. So the report half is held to writing nothing at all, by hashing the file."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    written = config.save_setting("library_root", str(tmp_path / "somewhere"))
    before = hashlib.sha256(written.read_bytes()).hexdigest()

    assert main(["config"]) == 0

    assert hashlib.sha256(written.read_bytes()).hexdigest() == before
    assert "wrote " not in capsys.readouterr().out


def test_a_setter_names_the_file_and_what_changed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.save_setting("library_root", str(tmp_path / "was here"))

    assert main(["config", "--library", str(tmp_path / "is here now")]) == 0

    said = capsys.readouterr().out
    assert f"wrote {tmp_path / 'noaap' / 'config.toml'}" in said
    assert f"library_root: {tmp_path / 'was here'} → {tmp_path / 'is here now'}" in said


def test_on_a_fresh_installation_the_old_value_is_the_default(tmp_path, monkeypatch, capsys):
    """Which is what somebody needs to see: not "unset", but what the program was doing until now.
    `(not set)` is printed only where there is nothing to do, as for a library root."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert main(["config", "--lyrics", "off"]) == 0
    assert "lyrics: True → False" in capsys.readouterr().out

    assert main(["config", "--library", str(tmp_path / "here")]) == 0
    assert f"library_root: (not set) → {tmp_path / 'here'}" in capsys.readouterr().out
