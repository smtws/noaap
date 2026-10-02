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
from noaap.web import WRITE_HEADER

# -- the settings file -----------------------------------------------------------------------------


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    ours, theirs = config.config_path(), config.legacy_config_path()
    theirs.parent.mkdir(parents=True)
    return ours, theirs


def test_ytalbums_settings_are_not_read_any_more(dirs):
    """They were, while ours did not exist, so a renamed machine was not met with "set the library
    first". The only installations of that file left are this project's own (R-420)."""
    ours, theirs = dirs
    theirs.write_text('library_root = "/tmp/somewhere"\nconcurrency = 4\n')

    assert config.read_path() == ours
    assert config.load().concurrency != 4, "nothing of theirs decides anything here"
    assert not ours.exists(), "and reading is still not writing"


def test_the_notice_names_the_file_nothing_reads_and_how_to_end_it(dirs):
    _, theirs = dirs
    theirs.write_text("concurrency = 4\n")

    notice = config.legacy_notice()

    assert notice and str(theirs) in notice and "noaap migrate" in notice
    assert "no longer read" in notice


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


def test_ytalbums_variables_are_not_read_and_are_named_once(monkeypatch, capsys):
    """They used to work and say so. Now they do nothing and say so — the louder of the two, because
    a variable that is silently ignored is a setting somebody thinks is in force (R-420)."""
    monkeypatch.setattr(config, "_said", set())
    monkeypatch.delenv("NOAAP_LRCLIB_BASE", raising=False)
    monkeypatch.setenv("YTALBUM_LRCLIB_BASE", "http://127.0.0.1:1/api")

    assert config.env("LRCLIB_BASE") is None
    assert config.env("LRCLIB_BASE") is None

    err = capsys.readouterr().err
    assert err.count("YTALBUM_LRCLIB_BASE") == 1, "once per name, not once per lookup"
    assert "NOAAP_LRCLIB_BASE" in err, "the notice says what the name is"


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


def test_a_write_is_accepted_under_our_header(server):
    _, client = server

    assert post(client, WRITE_HEADER).status_code == 202          # queued, which is a write


def test_the_old_header_is_not_accepted_any_more(server):
    """It was, while an installed PWA might still serve ytalbum's app.js from its own cache. No
    such page is left, and one spelling is one thing to reason about (R-420)."""
    _, client = server

    assert post(client, "X-Ytalbum").status_code == 403


def test_a_write_with_no_header_is_still_refused_and_the_message_names_ours(server):
    _, client = server

    refused = client.post("/api/update", content=b"{}", headers={"Content-Type": "application/json"})

    assert refused.status_code == 403
    assert WRITE_HEADER in refused.text and "Ytalbum" not in refused.text


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
    assert PLAN_FILE == ".noaap.json"


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
        "config.py":   "LEGACY and LEGACY_ENV: what `migrate` copies from, and the line that names "
                       "a file or a variable nothing reads any more",
        "systemd.py":  "LEGACY_UNIT: ytalbum's units are reported, never removed",
        "desktop.py":  "LEGACY_APP_ID: its launcher and profile are reported, never removed",
        "download.py": "OLD_PLAN_FILE: the name `migrate` renames and a pass names, never reads",
        "strays.py":   "prose: why a collection ytalbum named already looks like noaap's own scheme",
        "cli.py":      "the migrate subcommand and what 0.9.1 could read of a plan",
        "app.js":      "a comment: the two browser keys were read under the old names for one release",
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
        '"ytalbum"',          # config.LEGACY, systemd.LEGACY_UNIT, desktop.LEGACY_APP_ID — what
                              # `noaap migrate` copies from and reports, and never reads as settings
        '"YTALBUM_"',         # config.LEGACY_ENV — named in a line, not read (R-420)
        '".ytalbum.json"',    # download.OLD_PLAN_FILE — renamed by `migrate`, never read
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
