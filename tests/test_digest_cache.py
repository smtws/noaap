"""A file's digest is kept while the file is the same (§9, slice 201; q22 (iii), R-676).

A deep check digested every file of every folder album again — an ffmpeg remux that reads the whole
file, ≈0.27 s a file on the share and ≈1.3 s for a large flac — for files that had not changed. The
digest is now kept under the file's path, size and mtime; any of those changed and it is made again.
"""

from __future__ import annotations

import os
import shutil

import pytest
from test_incremental import opus_template

from noaap import sources_folder


@pytest.fixture
def made(monkeypatch):
    """`stream_sha`, counted: what the cache saves is exactly these calls."""
    calls: list[str] = []

    def stream_sha(path):
        calls.append(str(path))
        return f"sha-{path.stat().st_size}-{len(calls)}"

    monkeypatch.setattr(sources_folder, "stream_sha", stream_sha)
    return calls


@pytest.fixture
def a_file(tmp_path, opus_template):
    path = tmp_path / "album" / "01 One.opus"
    path.parent.mkdir()
    shutil.copy(opus_template, path)
    return path


def test_an_unchanged_file_is_not_digested_again(a_file, made):
    first = sources_folder.measure(a_file)
    second = sources_folder.measure(a_file)
    assert len(made) == 1
    assert first.stream_sha == second.stream_sha


def test_a_touched_file_is_digested_again(a_file, made):
    sources_folder.measure(a_file)
    st = a_file.stat()
    os.utime(a_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    sources_folder.measure(a_file)
    assert len(made) == 2


def test_a_file_of_another_size_is_digested_again(a_file, made):
    sources_folder.measure(a_file)
    st = a_file.stat()
    with a_file.open("ab") as f:
        f.write(b"\0" * 16)
    os.utime(a_file, ns=(st.st_atime_ns, st.st_mtime_ns))   # the same mtime: the size alone tells
    sources_folder.measure(a_file)
    assert len(made) == 2


def test_another_file_at_the_same_path_is_digested_again(a_file, made, tmp_path, opus_template):
    sources_folder.measure(a_file)
    other = tmp_path / "other.opus"
    shutil.copy(opus_template, other)
    with other.open("ab") as f:
        f.write(b"\0" * 8)
    os.replace(other, a_file)
    sources_folder.measure(a_file)
    assert len(made) == 2


def test_without_digests_nothing_is_asked_or_kept(a_file, made):
    assert sources_folder.measure(a_file, digest=False).stream_sha is None
    sources_folder.measure(a_file, digest=False)
    assert made == []
    sources_folder.measure(a_file)
    assert len(made) == 1, "and nothing was kept for a later pass that does want it"


def test_a_digest_that_could_not_be_made_is_not_kept(a_file, monkeypatch):
    calls = []
    monkeypatch.setattr(sources_folder, "stream_sha", lambda p: calls.append(p) or None)
    sources_folder.measure(a_file)
    sources_folder.measure(a_file)
    assert len(calls) == 2
