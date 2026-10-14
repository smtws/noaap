"""An update pass reads the library's plans once (§9, slice 197; q22, R-665).

r660, the library-wide deep check of 2026-10-13, took 11.5 h. About 4.5 h of it went to reading the
library: `_spellings`, `_named_like` and `find_plan` each read every one of the 1547 plans again for
every album (≈10 s per album on the share). The pass now reads them once and asks that one read.
"""

from __future__ import annotations

from test_incremental import FakeYouTube, opus_template, vol1

from noaap import download, service
from noaap.config import Config
from noaap.download import load_plan, run, save_plan
from noaap.plan import build_plan
from noaap.service import Service


def a_library(tmp_path, template, n=4):
    """`n` copies of one album under their own names and sources: n albums of one artist."""
    for i in range(n):
        collection = vol1()
        collection.source_id = collection.source_url = f"PLAYLIST{i}"
        plan = build_plan(collection)
        plan.album = plan.auto["album"] = f"Vol. {i}"
        plan.folder = f"{plan.albumartist}/Vol. {i}"
        run(plan, tmp_path / plan.folder, FakeYouTube(template))
    return tmp_path


class Echo(FakeYouTube):
    """Every playlist answers with the album its address names."""

    def collection(self, url):
        c = vol1()
        c.source_id = c.source_url = url
        return c


def counted(monkeypatch):
    reads = {"n": 0}
    real = download.iter_plans

    def iter_plans(library):
        reads["n"] += 1
        yield from real(library)

    monkeypatch.setattr(service, "iter_plans", iter_plans)
    monkeypatch.setattr(download, "iter_plans", iter_plans)
    return reads


def test_a_deep_check_reads_the_library_once(tmp_path, opus_template, monkeypatch):
    lib = a_library(tmp_path, opus_template)
    reads = counted(monkeypatch)

    Service(Config(musicbrainz=False, lyrics=False), lib, yt=Echo(opus_template)).update_all(
        report_only=True, deep=True)

    assert reads["n"] == 1, f"{reads['n']} reads of the library for 4 albums"


def test_an_apply_reads_it_once_too(tmp_path, opus_template, monkeypatch):
    lib = a_library(tmp_path, opus_template)
    reads = counted(monkeypatch)

    Service(Config(musicbrainz=False, lyrics=False), lib, yt=Echo(opus_template)).update_all(deep=True)

    assert reads["n"] == 1


def test_an_apply_sees_what_it_has_written_to_an_earlier_album(tmp_path, opus_template):
    """The index follows the disk during an apply: an album the pass has just written is asked as it
    now is, which is what the per-album read gave."""
    lib = a_library(tmp_path, opus_template, n=2)
    svc = Service(Config(musicbrainz=False, lyrics=False), lib, yt=Echo(opus_template))

    seen: list[str] = []
    real = svc.fetch

    def fetch(url, **kw):
        out = real(url, **kw)
        if url == "PLAYLIST0":
            plan = load_plan(out.album_dir)
            plan.albumartist = plan.auto["albumartist"] = "MY DARK LULLABIES"
            save_plan(plan, out.album_dir)
        else:
            seen.extend(svc._spellings(out.plan))
        return out

    svc.fetch = fetch
    svc.update_all(deep=True)

    assert "MY DARK LULLABIES" in seen, "the second album is asked about the first as the disk now has it"


def test_outside_a_pass_every_question_still_reads_the_disk(tmp_path, opus_template, monkeypatch):
    lib = a_library(tmp_path, opus_template, n=2)
    svc = Service(Config(musicbrainz=False, lyrics=False), lib, yt=Echo(opus_template))
    reads = counted(monkeypatch)

    svc.fetch("PLAYLIST0", report_only=True)
    svc.fetch("PLAYLIST1", report_only=True)

    assert reads["n"] >= 2, "a lone fetch is not a pass: it reads as it always did"
