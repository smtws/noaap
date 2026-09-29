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


# -- a setting the file cannot say is not a setting (§9, slice 71) -----------------------------------


def test_every_setting_can_be_written_in_the_config_file(tmp_path, monkeypatch):
    """`patreon_cookies_from_browser` and `patreon_post_cap` were on `Config`, in the README's
    settings table and in the provider's `settings()` — and `load()` never read either out of the
    file. The live Patreon run began with a config file that did nothing, and only a field-by-field
    comparison said why. So every field is written into a file here and read back.

    `watches` is the one exception: it is a list of tables with its own reader (`_watches`), tested
    where the watches are.
    """
    import dataclasses

    fields = [f for f in dataclasses.fields(config.Config) if f.name != "watches"]
    lines, expected = [], {}
    for f in fields:
        kind, default = str(f.type), getattr(config.Config(), f.name)
        if "Path" in kind:
            value = tmp_path / f"a-{f.name}"
            lines.append(f'{f.name} = "{value}"')
            expected[f.name] = value
        elif "bool" in kind:
            value = not bool(default)
            lines.append(f"{f.name} = {str(value).lower()}")
            expected[f.name] = value
        elif "int" in kind:
            value = int(default or 0) + 1
            lines.append(f"{f.name} = {value}")
            expected[f.name] = value
        elif "float" in kind:
            value = float(default or 0) + 1
            lines.append(f"{f.name} = {value}")
            expected[f.name] = value
        else:
            value = f"a-{f.name}"
            lines.append(f'{f.name} = "{value}"')
            expected[f.name] = value

    path = tmp_path / "config.toml"
    path.write_text("\n".join(lines) + "\n")
    cfg = config.load(path)

    deaf = {name: (want, getattr(cfg, name)) for name, want in expected.items()
            if getattr(cfg, name) != want}
    assert not deaf, f"load() ignores what the file says: {deaf}"


# -- a session that is set and does not work (§9, slice 71) -----------------------------------------


def test_a_browser_whose_cookies_cannot_be_read_is_named_in_the_report(tmp_path, monkeypatch, capsys):
    """The live Patreon run started with `patreon_cookies_from_browser = "chrome"`, a logged-in
    Chrome and no session: `secretstorage` was missing, so yt-dlp could not decrypt a single `v11`
    cookie and handed over a jar without the session — and said so in a warning nobody reads. The
    one sentence that tells a wrong setting from an unreadable store is printed where somebody is
    already looking, next to ffmpeg."""
    from noaap import ytdlp

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(ytdlp.sys, "platform", "linux")
    monkeypatch.setitem(__import__("sys").modules, "secretstorage", None)
    config.save_setting("patreon_cookies_from_browser", "chrome")

    assert main(["config"]) == 0

    said = capsys.readouterr().out
    assert "patreon_cookies_from_browser" in said and "secretstorage" in said

    # and firefox, whose store needs no keyring, is never complained about
    assert ytdlp.browser_cookie_trouble("firefox") is None
    assert ytdlp.browser_cookie_trouble(None) is None
