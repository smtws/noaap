"""`noaap migrate`: take over ytalbum's machine without taking anything away (DESIGN §9, slice 52).

Every case here is about restraint. The command copies, reports what it did not touch, and can be
undone by doing nothing — because the only thing `--uninstall-old` removes is a unit file and a
launcher entry, both of which ytalbum writes again on demand.

The dry run is the default, so the first case is that a plain `migrate` is a read.
"""
from __future__ import annotations

import subprocess

import pytest

from noaap import config, desktop, migrate, systemd


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine that was set up as ytalbum: settings, a vendor key, both caches, units, launcher."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(systemd, "systemctl",
                        lambda *a: subprocess.CompletedProcess(a, 0, "inactive\n", ""))

    theirs = config.config_dir(config.LEGACY)
    theirs.mkdir(parents=True)
    (theirs / "config.toml").write_text('library_root = "/tmp/music"\n')
    (theirs / "deepgram.env").write_text("YTALBUM_DEEPGRAM_KEY=not-a-real-key\n")
    (theirs / "config.toml.bak-2026-09-27").write_text("concurrency = 1\n")

    cache = config.cache_dir(config.LEGACY)
    cache.mkdir(parents=True)
    (cache / "lyrics.sqlite3").write_bytes(b"SQLite format 3\x00lyrics")
    (cache / "lyrics.sqlite3-wal").write_bytes(b"wal")
    (cache / "musicbrainz.sqlite3").write_bytes(b"SQLite format 3\x00mb")
    (cache / "pot-server.log").write_text("noise\n")
    (cache / "pot-server.heartbeat").write_text("")

    units = systemd.unit_dir()
    units.mkdir(parents=True)
    (units / "ytalbum.socket").write_text("[Socket]\nListenStream=127.0.0.1:8765\n")
    (units / "ytalbum.service").write_text("[Service]\nExecStart=/old/bin/ytalbum serve\n")

    data = desktop.data_home()
    (data / "applications").mkdir(parents=True)
    (data / "applications" / "ytalbum.desktop").write_text("[Desktop Entry]\nName=ytalbum\n")
    icon = data / "icons" / "hicolor" / "48x48" / "apps" / "ytalbum.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"\x89PNG")
    (data / "ytalbum-browser" / "Default").mkdir(parents=True)
    (data / "ytalbum-browser" / "Default" / "Cookies").write_bytes(b"secrets")
    return tmp_path


# -- the default is a read -------------------------------------------------------------------------


def test_a_plain_migrate_changes_nothing(machine):
    lines = migrate.run()

    assert not config.config_dir().exists() and not config.cache_dir().exists()
    assert (systemd.unit_dir() / "ytalbum.socket").exists()
    assert all(line.startswith(("would ", "left alone", "ytalbum's units")) or "nothing was changed" in line
               for line in lines), lines
    assert lines[-1] == "nothing was changed. `noaap migrate --apply` does it."


def test_it_says_what_it_would_take_and_what_it_would_not(machine):
    said = "\n".join(migrate.run())

    assert "config.toml" in said and "deepgram.env" in said
    assert "lyrics.sqlite3" in said and "musicbrainz.sqlite3" in said
    assert "config.toml.bak-2026-09-27" not in said, "a backup the user made is theirs, not ours"
    assert "pot-server.log" not in said and "heartbeat" not in said, "runtime scratch"
    assert "ytalbum-browser" in said and "holds your cookies" in said


# -- what --apply does -----------------------------------------------------------------------------


def test_apply_copies_the_settings_the_key_and_both_caches(machine):
    migrate.run(apply=True)

    assert (config.config_dir() / "config.toml").read_text() == 'library_root = "/tmp/music"\n'
    assert (config.config_dir() / "deepgram.env").exists()
    assert (config.cache_dir() / "lyrics.sqlite3").read_bytes().startswith(b"SQLite format 3")
    assert (config.cache_dir() / "lyrics.sqlite3-wal").exists(), "a db left open is not half-copied"
    assert (config.cache_dir() / "musicbrainz.sqlite3").exists()
    assert config.read_path() == config.config_path(), "and the fallback is over"


def test_the_originals_are_still_there_afterwards(machine):
    migrate.run(apply=True)

    theirs = config.config_dir(config.LEGACY)
    assert (theirs / "config.toml").exists() and (theirs / "deepgram.env").exists()
    assert (config.cache_dir(config.LEGACY) / "lyrics.sqlite3").exists()
    assert migrate.run()[-1] == "nothing was changed. `noaap migrate --apply` does it."


def test_it_never_overwrites_what_we_already_have(machine):
    config.config_dir().mkdir(parents=True)
    (config.config_dir() / "config.toml").write_text("concurrency = 9\n")

    lines = migrate.run(apply=True)

    assert (config.config_dir() / "config.toml").read_text() == "concurrency = 9\n"
    assert any("skipped" in line and "already here" in line for line in lines)


def test_applying_twice_does_nothing_the_second_time(machine):
    migrate.run(apply=True)
    again = migrate.run(apply=True)

    assert not any(line.startswith("copied") for line in again)
    assert all("skipped" in line or line.startswith(("left alone", "ytalbum's units", "to go back"))
               for line in again), again


# -- and the old installation, only when asked -----------------------------------------------------


def test_the_units_and_the_launcher_stay_unless_asked(machine):
    migrate.run(apply=True)

    assert (systemd.unit_dir() / "ytalbum.socket").exists()
    assert (desktop.data_home() / "applications" / "ytalbum.desktop").exists()


def test_uninstall_old_removes_the_units_and_the_launcher_and_says_how_to_go_back(machine):
    lines = migrate.run(apply=True, uninstall_old=True)

    assert systemd.legacy_units() == []
    assert not (desktop.data_home() / "applications" / "ytalbum.desktop").exists()
    assert not (desktop.data_home() / "icons" / "hicolor" / "48x48" / "apps" / "ytalbum.png").exists()
    assert any("ytalbum service install" in line for line in lines), "the undo is in the output"


def test_uninstall_old_never_touches_the_browser_profile_or_the_old_directories(machine):
    """The profile is the user's logins. The directories are what going back to ytalbum needs."""
    migrate.run(apply=True, uninstall_old=True)

    assert (desktop.data_home() / "ytalbum-browser" / "Default" / "Cookies").read_bytes() == b"secrets"
    assert (config.config_dir(config.LEGACY) / "config.toml").exists()
    assert (config.cache_dir(config.LEGACY) / "lyrics.sqlite3").exists()


def test_a_dry_run_with_uninstall_old_still_removes_nothing(machine):
    lines = migrate.run(uninstall_old=True)

    assert systemd.legacy_units(), "still there"
    assert (desktop.data_home() / "applications" / "ytalbum.desktop").exists()
    assert any(line.startswith("would remove") for line in lines)
    assert any(line.startswith("would stop and disable") for line in lines)


def test_on_a_machine_that_never_had_the_old_program_it_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(systemd, "systemctl",
                        lambda *a: subprocess.CompletedProcess(a, 0, "inactive\n", ""))

    lines = migrate.run(apply=True, uninstall_old=True)

    assert not any(line.startswith(("copied", "removed", "stopped")) for line in lines)
    assert not config.config_dir().exists()


# -- the key is a file name here, never a value ----------------------------------------------------


def test_the_vendor_key_is_copied_by_bytes_and_never_read(machine, capsys):
    """Its name is in the output because the user needs to know it moved. Its contents are not."""
    lines = migrate.run(apply=True)

    assert "not-a-real-key" not in "\n".join(lines)
    assert "not-a-real-key" not in capsys.readouterr().out
    assert (config.config_dir() / "deepgram.env").read_text() == "YTALBUM_DEEPGRAM_KEY=not-a-real-key\n"


def test_the_command_itself_is_a_dry_run(machine, capsys):
    from noaap import cli

    assert cli.main(["migrate"]) == 0

    out = capsys.readouterr().out
    assert "would copy" in out and "nothing was changed" in out
    assert not config.config_dir().exists()
