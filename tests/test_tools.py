"""What `noaap config` says it found, and what it does when it finds nothing.

ffmpeg is the one dependency that is *reported and not required*: a library can be browsed, tagged,
searched and have its lyrics fetched without it. Downloading and trimming are what stop — and they
stop at the moment of use, which is a bad moment to learn about it. Until v0.8.0 nothing checked at
all, and a cold reader found the README claiming otherwise (docs/qa-catalog.md, AO then AP).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from noaap.cli import main
from noaap.config import Config


def without(monkeypatch: pytest.MonkeyPatch, *missing: str) -> None:
    """`shutil.which` as if those programs were not installed."""
    real = shutil.which
    monkeypatch.setattr("noaap.config.shutil.which",
                        lambda name, *a, **k: None if name in missing else real(name, *a, **k))


def test_ffmpeg_is_found_when_it_is_there(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noaap.config.shutil.which",
                        lambda name, *a, **k: "/somewhere/bin/ffmpeg" if name == "ffmpeg" else None)
    assert Config().resolved_ffmpeg() == "/somewhere/bin/ffmpeg"


def test_ffmpeg_is_none_when_it_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    without(monkeypatch, "ffmpeg")
    assert Config().resolved_ffmpeg() is None


def test_config_reports_where_ffmpeg_is(monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("noaap.config.shutil.which",
                        lambda name, *a, **k: f"/usr/bin/{name}")
    assert main(["config"]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("ffmpeg:"))
    assert "/usr/bin/ffmpeg" in line


def test_config_says_what_breaks_when_ffmpeg_is_missing(monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    """Not just "not found": the point is which two things stop, and how to fix it."""
    without(monkeypatch, "ffmpeg")
    assert main(["config"]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("ffmpeg:"))
    assert "NOT FOUND" in line
    assert "downloading and trimming" in line
    assert "install ffmpeg" in line


def test_a_missing_ffmpeg_is_reported_and_not_an_error(monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """`config` still exits 0 — everything that does not touch audio still works."""
    without(monkeypatch, "ffmpeg", "node", "deno", "bun", "quickjs")
    assert main(["config"]) == 0
    said = capsys.readouterr().out
    assert "NOT FOUND" in said              # ffmpeg
    assert "NONE FOUND" in said             # and the JS runtime, which is also only reported


def test_the_source_tree_writes_slice_references_the_way_the_docs_do() -> None:
    """A bare section-dot-number reference points at a numbered list item under a heading called
    `## 9.`, which no reader can find; `§9, slice 44` can be found. The docs were fixed in P44 and
    the code in P45, and this keeps them in step.

    The example is spelled out rather than written literally on purpose: a scanner that matches its
    own description is the `pkill -f` mistake in another costume.
    """
    import re

    root = Path(__file__).parent.parent
    offenders = []
    for path in list((root / "src").rglob("*.py")) + list((root / "src/noaap/webui").glob("*.*")) \
            + list((root / "tests").rglob("*.py")) + list((root / "tests/js").glob("*.mjs")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"§9\.\d", line):
                offenders.append(f"{path.relative_to(root)}:{n}")
    assert not offenders, f"write '§9, slice N': {offenders[:5]}"
