"""`scripts/release-install.sh`: what it refuses before it builds anything (DESIGN §9, slice 93).

The script exists so the user's service runs a released snapshot instead of the working tree. Its
refusals are the whole safety of it — a release built from a dirty tree, from an untagged commit, or
into a venv on a temporary filesystem is exactly what it is there to prevent — so they are held here,
on throwaway repositories, without building or installing anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "release-install.sh"


def run(repo: Path, *args: str, home: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, HOME=str(home or repo.parent / "home"))
    env.pop("NOAAP_RELEASE_VENV", None)
    return subprocess.run(["bash", str(repo / "scripts" / "release-install.sh"), *args],
                          capture_output=True, text=True, cwd=repo, env=env, timeout=60)


@pytest.fixture
def repo(tmp_path):
    """A repository that looks like this one from the script's point of view, and holds nothing."""
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "release-install.sh")
    (tmp_path / "home").mkdir()
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run([*git, "config", "user.name", "Test"], check=True)
    (root / "pyproject.toml").write_text('[project]\nname = "noaap"\nversion = "9.9.9"\n')
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "one"], check=True)
    return root


def test_a_dirty_checkout_is_refused(repo):
    (repo / "pyproject.toml").write_text('[project]\nname = "noaap"\nversion = "9.9.10"\n')
    done = run(repo, "--allow-untagged")
    assert done.returncode != 0
    assert "uncommitted changes" in done.stderr
    assert "--allow-dirty" in done.stderr


def test_an_untagged_commit_is_refused_unless_it_is_asked_for(repo):
    done = run(repo)
    assert done.returncode != 0
    assert "not a tagged commit" in done.stderr


def test_a_tag_that_does_not_exist_is_refused(repo):
    done = run(repo, "v9.9.9")
    assert done.returncode != 0
    assert "no tag or commit named v9.9.9" in done.stderr


def test_a_venv_outside_the_home_or_in_a_temporary_filesystem_is_refused(repo, tmp_path):
    """uv hardlinks a venv's files from its cache on one filesystem and copies them across two: a
    release venv on tmpfs writes the whole of torch, which is how this was found."""
    home = tmp_path / "home"
    done = run(repo, "--allow-untagged", "--venv", str(tmp_path / "elsewhere"), home=home)
    assert done.returncode != 0 and "must live under" in done.stderr

    done = run(repo, "--allow-untagged", "--venv", "/tmp/noaap-release", home=home)
    assert done.returncode != 0
    assert "must live under" in done.stderr or "temporary filesystem" in done.stderr

    # a path that is under $HOME and still in a temporary filesystem is refused by name
    done = run(repo, "--allow-untagged", "--venv", "/tmp/home/ok", home=Path("/tmp/home"))
    assert "temporary filesystem" in done.stderr


def test_one_tag_at_a_time_and_unknown_options_are_refused(repo):
    assert "one tag at a time" in run(repo, "a", "b").stderr
    assert "unknown option" in run(repo, "--wat").stderr
    assert "--venv needs a path" in run(repo, "--venv").stderr


def test_it_explains_itself_without_doing_anything(repo):
    done = run(repo, "--help")
    assert done.returncode == 0
    assert "not from the working tree" in done.stdout
    assert "release-install.sh" in done.stdout
    assert "set -euo" not in done.stdout          # the comment block, not the code
    assert not (repo.parent / "home" / ".local").exists()   # nothing was created


def test_the_script_and_the_unit_agree_on_where_a_release_lives(tmp_path):
    """One source of truth for the venv's path: the script takes `NOAAP_RELEASE_VENV`, and so does the
    module that writes the unit — otherwise a release could be installed where nothing starts it."""
    text = SCRIPT.read_text()
    assert "NOAAP_RELEASE_VENV" in text
    assert "$HOME/.local/noaap-release" in text

    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "bin").mkdir(parents=True)
    (elsewhere / "bin" / "noaap").write_text("#!/bin/sh\n")
    said = subprocess.run(
        [sys.executable, "-c",
         "from noaap.systemd import render_units\n"
         "from noaap.config import Config\n"
         "print([l for l in render_units(Config())['noaap.service'].splitlines()"
         " if l.startswith('ExecStart')][0])\n"],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, NOAAP_RELEASE_VENV=str(elsewhere),
                 PYTHONPATH=str(Path(__file__).parent.parent / "src")))
    assert said.returncode == 0, said.stderr
    assert str(elsewhere / "bin" / "noaap") in said.stdout


def test_the_helpers_are_a_list_the_caller_can_replace():
    """The extras are the package's; the helpers are the *machine's* (§9, slice 93, P74b) — two CUDA 12
    wheels ctranslate2 needs where torch brings CUDA 13's, and the keyring client yt-dlp reads Chrome's
    cookies with. A release that cannot get one of them should stop, not run quietly on the processor."""
    text = SCRIPT.read_text()
    assert 'HELPERS="${NOAAP_RELEASE_HELPERS:-nvidia-cublas-cu12 nvidia-cudnn-cu12 secretstorage}"' in text
    # one resolution for the wheel and the helpers, so a missing helper fails the install
    assert 'uv pip install --quiet --python "$VENV/bin/python" "$spec" $HELPERS' in text


def test_the_helper_list_needs_a_value_and_the_help_explains_it(repo):
    assert '--helpers needs a list' in run(repo, "--helpers").stderr
    said = run(repo, "--help").stdout
    for helper in ("nvidia-cublas-cu12", "nvidia-cudnn-cu12", "secretstorage"):
        assert helper in said
    assert "NOAAP_RELEASE_HELPERS" in said
