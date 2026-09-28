"""What still answers to ytalbum, and what does not (DESIGN §9, slice 52).

The rename is only kind if a machine that was set up as ytalbum keeps working without being told
anything. Three things carry over — the settings file, the `YTALBUM_*` variables and the write
header — and each is read but never written, so the old spelling fades instead of being maintained.

Two things deliberately do NOT carry over, and are pinned here so they are not added later by
sympathy: the caches (copied by `noaap migrate`, else refilled) and the plan file, which never
moved in the first place because it is the format's name.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from test_incremental import opus_template  # a fixture, used by name
from test_web import library, server  # likewise: a real server on a real library

from noaap import config, user_agent, version
from noaap.download import PLAN_FILE
from noaap.web import LEGACY_WRITE_HEADER, WRITE_HEADER

# -- the settings file -----------------------------------------------------------------------------


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    ours, theirs = config.config_path(), config.legacy_config_path()
    theirs.parent.mkdir(parents=True)
    return ours, theirs


def test_settings_are_read_from_ytalbums_directory_while_we_have_none(dirs):
    ours, theirs = dirs
    theirs.write_text('library_root = "/tmp/somewhere"\nconcurrency = 4\n')

    assert config.read_path() == theirs
    assert config.load().concurrency == 4, "a machine set up as ytalbum needs no migration to run"
    assert not ours.exists(), "reading is not writing"


def test_the_notice_names_the_file_it_read_and_how_to_end_it(dirs):
    _, theirs = dirs
    theirs.write_text("concurrency = 4\n")

    notice = config.legacy_notice()

    assert notice and str(theirs) in notice and "noaap migrate" in notice


def test_our_own_settings_win_and_the_notice_goes_away(dirs):
    ours, theirs = dirs
    theirs.write_text("concurrency = 4\n")
    ours.parent.mkdir(parents=True)
    ours.write_text("concurrency = 9\n")

    assert config.read_path() == ours
    assert config.load().concurrency == 9
    assert config.legacy_notice() is None


def test_nothing_is_ever_written_back_into_ytalbums_directory(dirs):
    ours, theirs = dirs
    theirs.write_text("concurrency = 4\n")
    before = theirs.read_text()

    written = config.save_setting("concurrency", 7)

    assert written == ours, "a save lands in our directory even while we read theirs"
    assert theirs.read_text() == before
    assert config.load().concurrency == 7, "and it is the one that counts from now on"


def test_with_no_settings_anywhere_the_path_is_ours(dirs):
    ours, _ = dirs
    assert config.read_path() == ours and config.legacy_notice() is None


# -- the environment variables ---------------------------------------------------------------------


def test_ytalbums_variables_still_work_and_say_so_once(monkeypatch, capsys):
    monkeypatch.setattr(config, "_said", set())
    monkeypatch.delenv("NOAAP_LRCLIB_BASE", raising=False)
    monkeypatch.setenv("YTALBUM_LRCLIB_BASE", "http://127.0.0.1:1/api")

    assert config.env("LRCLIB_BASE") == "http://127.0.0.1:1/api"
    assert config.env("LRCLIB_BASE") == "http://127.0.0.1:1/api"

    err = capsys.readouterr().err
    assert err.count("YTALBUM_LRCLIB_BASE") == 1, "once per name, not once per lookup"
    assert "NOAAP_LRCLIB_BASE" in err, "the notice says what to rename it to"


def test_our_own_variable_wins_and_is_silent(monkeypatch, capsys):
    monkeypatch.setattr(config, "_said", set())
    monkeypatch.setenv("YTALBUM_LRCLIB_BASE", "http://127.0.0.1:1/api")
    monkeypatch.setenv("NOAAP_LRCLIB_BASE", "http://127.0.0.1:2/api")

    assert config.env("LRCLIB_BASE") == "http://127.0.0.1:2/api"
    assert capsys.readouterr().err == ""


def test_an_unset_variable_is_none_under_either_name(monkeypatch):
    monkeypatch.delenv("NOAAP_LRCLIB_BASE", raising=False)
    monkeypatch.delenv("YTALBUM_LRCLIB_BASE", raising=False)
    assert config.env("LRCLIB_BASE") is None


# -- the write header ------------------------------------------------------------------------------


def post(client, header):
    return client.post("/api/update", content=b"{}",
                       headers={header: "1", "Content-Type": "application/json"})


def test_a_write_is_accepted_under_either_header(server):
    """An installed PWA keeps serving ytalbum's app.js from its own cache until the service worker
    updates itself, so for a while the page in front of the user sends the old header."""
    _, client = server

    assert post(client, WRITE_HEADER).status_code == 202          # queued, which is a write
    assert post(client, LEGACY_WRITE_HEADER).status_code == 202


def test_a_write_with_no_header_is_still_refused_and_the_message_names_ours(server):
    _, client = server

    refused = client.post("/api/update", content=b"{}", headers={"Content-Type": "application/json"})

    assert refused.status_code == 403
    assert WRITE_HEADER in refused.text and LEGACY_WRITE_HEADER not in refused.text


# -- who we say we are -----------------------------------------------------------------------------


def test_the_user_agent_carries_our_version_and_a_contact_that_exists():
    """LRCLIB and MusicBrainz both ask for a contact address. ytalbum's said `0.1` forever and
    pointed at a repository that had been renamed, which is a defect in a live request."""
    agent = user_agent()

    assert agent.startswith(f"noaap/{version()} ")
    assert "github.com/smtws/noaap" in agent
    assert version() != "0.1" and "ytalbum" not in agent


def test_both_clients_use_it():
    from noaap import lyrics, mb

    assert lyrics.USER_AGENT == mb.USER_AGENT == user_agent()


def test_the_musicbrainz_edit_note_says_who_seeded_it():
    from noaap.mb import SEED_NOTE

    assert "noaap" in SEED_NOTE and "ytalbum" not in SEED_NOTE


# -- and what does not carry over ------------------------------------------------------------------


def test_the_caches_are_ours_alone(dirs):
    """No fallback here on purpose: two sqlite files read-both/write-new is complexity for nothing,
    and `noaap migrate` copies them in one step. Without it they refill."""
    from noaap import lyrics, mb, pot

    for path in (lyrics.default_cache_path(), mb.default_cache_path(), pot.cache_dir()):
        assert config.NAME in path.parts and config.LEGACY not in path.parts


def test_the_plan_file_keeps_the_name_the_format_was_born_with():
    """Renaming it would make every album this writes invisible to ytalbum 0.9.0 — `iter_plans`
    would find nothing and a re-fetch would build a second folder beside the first. Slice 48 says
    a library stays readable both ways, and this is what that costs: a file named after the name."""
    assert PLAN_FILE == ".ytalbum.json"


# -- the guard -------------------------------------------------------------------------------------


def test_what_still_answers_to_the_old_name_is_written_down() -> None:
    """The rename is only finished while the list of exceptions is a list, not a habit.

    This is the same shape as slice 51's grep guard, and for the same reason: nothing here is a type
    error, and a new mention of the old name reads as harmless in a diff. Two assertions —

    1. **which files** may say it at all, each with a reason below;
    2. **which identifier-like literals** exist, which is the behavioural half: `"ytalbum"` in a
       sentence is prose, `"ytalbum-last"` is a key something reads.

    `docs/` is deliberately out of scope. The catalog and DESIGN §9/§12 are records of what was run
    on a date, and rewriting them would make the record false; each carries a header saying so.
    """
    import re

    root = Path(__file__).parent.parent
    src = root / "src" / "noaap"

    allowed = {
        "migrate.py":  "the command whose whole subject is ytalbum's machine",
        "config.py":   "LEGACY, the settings fallback and the YTALBUM_* variables",
        "systemd.py":  "LEGACY_UNIT: ytalbum's units are reported, never removed",
        "desktop.py":  "LEGACY_APP_ID: its launcher and profile are reported, never removed",
        "web.py":      "LEGACY_WRITE_HEADER: an installed PWA still sends the old one",
        "download.py": "PLAN_FILE — the format's name, and it is not moving",
        "cli.py":      "the migrate subcommand, the notice, and the plan file in one message",
        "app.js":      "the two localStorage keys, read once under the old name",
        "sw.js":       "a comment only — why the cache name had to change; the literals below are the promise",
    }
    saying = sorted(p.name for p in src.rglob("*")
                    if p.is_file() and p.suffix in {".py", ".js", ".mjs", ".html", ".css", ".webmanifest"}
                    and "ytalbum" in p.read_text(encoding="utf-8").lower())
    assert saying == sorted(allowed), (
        "a file started answering to the old name, or stopped:\n"
        + "\n".join(f"  {name}: {allowed.get(name, '*** not written down ***')}" for name in saying))

    # identifier-like: no spaces. A sentence that mentions ytalbum is prose and not a promise.
    literals = {m for path in src.rglob("*") if path.is_file() and path.suffix in {".py", ".js", ".mjs"}
                for m in re.findall(r'"[^"\s]*ytalbum[^"\s]*"', path.read_text(encoding="utf-8"), re.I)}
    assert literals == {
        '"ytalbum"',        # config.LEGACY, systemd.LEGACY_UNIT, desktop.LEGACY_APP_ID
        '"YTALBUM_"',       # config.LEGACY_ENV
        '".ytalbum.json"',  # download.PLAN_FILE
        '"X-Ytalbum"',      # web.LEGACY_WRITE_HEADER
        '"ytalbum-last"',   # app.js: which album was open
        '"ytalbum-theme"',  # app.js: which theme was picked
    }, f"a new thing answers to the old name by value: {sorted(literals)}"


def test_the_workflow_and_the_issue_templates_carry_no_trace_of_it() -> None:
    """`.github/` is instructions to a machine and to whoever files a bug: no history to preserve.

    This exists because the rename missed exactly one line here — `node --check
    src/ytalbum/webui/app.js` — so three commits were green locally and red on the remote.
    """
    github = Path(__file__).parent.parent / ".github"
    offenders = [f"{p.relative_to(github)}:{n}: {line.strip()[:60]}"
                 for p in sorted(github.rglob("*")) if p.is_file()
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if "ytalbum" in line.lower()]
    assert not offenders, "the old name is still in .github/:\n" + "\n".join(offenders)
