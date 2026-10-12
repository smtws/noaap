"""An archive that is down has not answered (DESIGN §9, slice 187; q11, seen in I-471).

`Die Happy/No Nuts No Glory` and `Lordi/To Beast or Not to Beast`: release front 404, group front
**500**. The pass wrote `tried`, and the next check no longer offered the fetch — a server's bad
minute counted as the archive's answer.
"""

from __future__ import annotations

from test_cover_stays_true import BLUE, GROUP, RED, RELEASE, a_repair, ours_beside, taken_in, with_a_release

from noaap import download
from noaap.download import caa_group_front, caa_release_front, cover_would, load_plan

DOWN = "coverartarchive.org answered 500"
NONE = "coverartarchive.org answered 404"


def archive(answers):
    """`_archive_cover` as the archive answers: bytes, or 404/500/an exception's text for `why`."""
    asked: list[str] = []

    def fetch(url, why=None):
        asked.append(url)
        got = answers.get(url, 404)
        if isinstance(got, bytes):
            return got
        if why is not None:
            reason = f"coverartarchive.org answered {got}" if isinstance(got, int) else got
            why.append(getattr(download, "Outage", str)(reason) if got != 404 else reason)
        return None
    return fetch, asked


def test_a_group_that_is_down_writes_nothing_and_says_it_asks_again(tmp_path, one_second_of_sound, monkeypatch):
    """Die Happy and Lordi, as they were on 2026-10-12."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    ours_beside(album, RED, caa_group_front(GROUP))
    before = dict(load_plan(album).cover_fetched)
    fetch, _ = archive({caa_release_front(RELEASE): 404, caa_group_front(GROUP): 500})
    monkeypatch.setattr(download, "_archive_cover", fetch)

    said = a_repair(root, dry_run=False)

    assert load_plan(album).cover_fetched == before, "no `tried`, nothing else either"
    assert (album / "cover.jpg").read_bytes() == RED
    lines = [line.strip() for line in said if "asked again next time" in line]
    assert len(lines) == 1 and "answered 500" in lines[0] and caa_group_front(GROUP) in lines[0], said
    assert cover_would(load_plan(album), album), "and the next check offers the fetch again"


def test_a_release_that_is_down_is_not_passed_over_for_the_group(tmp_path, one_second_of_sound, monkeypatch):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    ours_beside(album, RED, caa_group_front(GROUP))
    before = dict(load_plan(album).cover_fetched)
    fetch, asked = archive({caa_release_front(RELEASE): 503, caa_group_front(GROUP): BLUE})
    monkeypatch.setattr(download, "_archive_cover", fetch)

    said = a_repair(root, dry_run=False)

    assert caa_group_front(GROUP) not in asked, "the group answers 'the release has none'; it has not said so"
    assert (album / "cover.jpg").read_bytes() == RED and load_plan(album).cover_fetched == before
    assert [line for line in said if "answered 503 — asked again next time" in line], said


def test_a_connection_that_fails_is_an_outage_too(tmp_path, one_second_of_sound, monkeypatch):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    ours_beside(album, RED, caa_group_front(GROUP))
    before = dict(load_plan(album).cover_fetched)
    fetch, _ = archive({caa_release_front(RELEASE): "timed out"})
    monkeypatch.setattr(download, "_archive_cover", fetch)

    a_repair(root, dry_run=False)

    assert load_plan(album).cover_fetched == before


def test_two_404s_still_end_the_offer(tmp_path, one_second_of_sound, monkeypatch):
    """What a 404 always did, and still does: the archive has no picture there."""
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    ours_beside(album, RED, caa_group_front(GROUP))
    fetch, _ = archive({})
    monkeypatch.setattr(download, "_archive_cover", fetch)

    a_repair(root, dry_run=False)

    assert load_plan(album).cover_fetched["tried"] == caa_release_front(RELEASE)
    assert cover_would(load_plan(album), album) == []


def test_the_archive_marks_a_server_error_as_an_outage(monkeypatch):
    import httpx
    from conftest import REAL_ARCHIVE_COVER

    class Answer:
        def __init__(self, code):
            self.status_code, self.content = code, b""

    for code, outage in ((500, True), (503, True), (429, True), (404, False)):
        monkeypatch.setattr(httpx, "get", lambda *a, code=code, **k: Answer(code))
        why: list[str] = []
        assert REAL_ARCHIVE_COVER(caa_release_front(RELEASE), why) is None
        assert (type(why[0]).__name__ == "Outage") is outage, (code, why)

    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out")))
    why = []
    REAL_ARCHIVE_COVER(caa_release_front(RELEASE), why)
    assert type(why[0]).__name__ == "Outage", why


def test_a_first_fetch_takes_the_group_but_does_not_record_a_release_that_was_down(
        tmp_path, one_second_of_sound, monkeypatch):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    with_a_release(album)
    fetch, _ = archive({caa_release_front(RELEASE): 500, caa_group_front(GROUP): BLUE})
    monkeypatch.setattr(download, "_archive_cover", fetch)

    a_repair(root, dry_run=False)

    assert (album / "cover.jpg").read_bytes() == BLUE, "a picture now is better than none"
    assert "tried" not in load_plan(album).cover_fetched, "the release was down, not empty"
    assert cover_would(load_plan(album), album), "so the release is asked again next time"


def test_a_first_fetch_where_everything_was_down_says_it_asks_again(tmp_path, one_second_of_sound, monkeypatch):
    root = tmp_path / "collection"
    album = taken_in(root, one_second_of_sound)
    with_a_release(album)
    fetch, _ = archive({caa_release_front(RELEASE): 500, caa_group_front(GROUP): 502})
    monkeypatch.setattr(download, "_archive_cover", fetch)

    said = a_repair(root, dry_run=False)

    assert not (album / "cover.jpg").exists()
    assert [line for line in said if "asked again next time" in line], said
    assert "tried" not in load_plan(album).cover_fetched
