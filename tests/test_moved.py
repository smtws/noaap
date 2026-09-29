"""A library that survives being moved (DESIGN §9, slice 60).

The fact this package exists for is not that a moved library breaks. **It is that a moved library
works** — by reading the files of the library it was copied from. Measured on a real one: all 2000
refs of a copied collection resolved, every one of them into the original, so the copy played,
measured and merged using somebody else's files until the day the original was deleted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from noaap.cli import _is_conversion, _plan_differences


def test_a_change_carries_both_values():
    """Whether a change is a fault or a conversion can only be told by looking at them."""
    gone, changed = _plan_differences({"a": "/x/y", "b": 1}, {"a": "y", "b": 2})

    assert gone == []
    assert sorted(changed) == [("a", "/x/y", "y"), ("b", 1, 2)]


@pytest.mark.parametrize("before,after,is_it", [
    ("/library/A Band/An Album/01.mp3", "01.mp3", True),          # relative to the album
    ("/library/A Band/An Album", "A Band/An Album", True),        # relative to the library
    ("/library/A Band/An Album/01.mp3", "02.mp3", False),         # a different file
    ("/elsewhere/01.mp3", "01.mp3", False),                       # not under this library at all
    ("/library/A Band/An Album/01.mp3", "/library/A Band/An Album/01.mp3", False),
    ("01.mp3", "01.mp3", False),
    ("/library/A Band/An Album/01.mp3", "", False),
    (7, 8, False),
])
def test_only_the_rewrite_itself_counts_as_a_conversion(before, after, is_it):
    """Nothing else may hide behind it: a value that changed for another reason is a change a
    person has to be told about."""
    album = Path("/library/A Band/An Album")

    assert _is_conversion(before, after, album, Path("/library")) is is_it


def test_verify_tells_a_conversion_from_a_loss(tmp_path, capsys):
    """R-207, ruling 2: a conversion is reported as a change, and it is not a fault."""
    from noaap.cli import _verify_plans
    from noaap.config import Config
    from noaap.download import PLAN_FILE, save_plan
    from noaap.models import AlbumPlan, Kind, PlanTrack

    album_dir = tmp_path / "A Band" / "An Album"
    track = PlanTrack(video_id="aaaaaaaaaaa", number=1, artist="A Band", title="One",
                      filename="01 - One.opus", provenance={}, state="done")
    plan = AlbumPlan(source_url="https://y/1", source_id="p1", kind=Kind.OFFICIAL_ALBUM,
                     album="An Album", albumartist="A Band", year=None, cover_url=None,
                     folder="A Band/An Album", tracks=[track])
    save_plan(plan, album_dir)

    assert _verify_plans(Config(), tmp_path) == 0
    said = capsys.readouterr().out
    assert "1 byte-identical" in said and "would become relative" not in said


# -- what is written, and what is read back --------------------------------------------------------


def an_album(root: Path, name: str = "An Album", provider: str = "folder"):
    from noaap.download import save_plan
    from noaap.models import AlbumPlan, Kind, PlanTrack

    album_dir = root / "A Band" / name
    album_dir.mkdir(parents=True, exist_ok=True)
    (album_dir / "01 - One.opus").write_bytes(b"audio")
    track = PlanTrack(video_id=str(album_dir / "01 - One.opus"), number=1, artist="A Band",
                      title="One", filename="01 - One.opus", provenance={}, state="done")
    plan = AlbumPlan(source_url=str(album_dir), source_id=str(album_dir), kind=Kind.OFFICIAL_ALBUM,
                     album=name, albumartist="A Band", year=None, cover_url=str(album_dir),
                     folder=f"A Band/{name}", tracks=[track], provider=provider)
    save_plan(plan, album_dir)
    return album_dir


def test_a_path_inside_the_album_is_written_relative_to_it(tmp_path):
    album_dir = an_album(tmp_path)

    written = json.loads((album_dir / ".ytalbum.json").read_text())

    assert written["source_id"] == "./" and written["source_url"] == "./"
    assert written["tracks"][0]["video_id"] == "./01 - One.opus"
    assert written["tracks"][0]["filename"] == "01 - One.opus", "a bare name was never a path"
    assert written["schema"] == 2


def test_and_read_back_as_the_file_it_names(tmp_path):
    """In memory a plan holds what it always held, so nothing that reads one had to change."""
    from noaap.download import load_plan

    album_dir = an_album(tmp_path)

    plan = load_plan(album_dir)

    assert plan.tracks[0].video_id == str(album_dir / "01 - One.opus")
    assert Path(plan.tracks[0].video_id).is_file()
    assert plan.source_id == str(album_dir)


def test_a_path_that_is_not_this_album_is_left_exactly_as_it_is(tmp_path):
    """An intake folder is somewhere else by nature, and it is not this library's to rewrite."""
    from noaap.download import load_plan, save_plan

    album_dir = an_album(tmp_path)
    plan = load_plan(album_dir)
    plan.source_url = plan.source_id = "/somewhere/else/An Album"
    plan.tracks[0].video_id = "/somewhere/else/An Album/01 - One.opus"
    save_plan(plan, album_dir)

    written = json.loads((album_dir / ".ytalbum.json").read_text())
    assert written["source_id"] == "/somewhere/else/An Album"
    assert written["tracks"][0]["video_id"] == "/somewhere/else/An Album/01 - One.opus"


# -- and what does not change ------------------------------------------------------------------------


def test_an_album_with_no_path_in_it_stays_schema_one_and_byte_identical(tmp_path):
    """R-207, ruling 1. Every YouTube and SoundCloud album is this one, and it stays readable by
    ytalbum 0.9.1 and by every noaap up to 1.5.0."""
    from noaap.download import load_plan, save_plan
    from noaap.models import AlbumPlan, Kind, PlanTrack

    album_dir = tmp_path / "A Band" / "An Album"
    track = PlanTrack(video_id="aaaaaaaaaaa", number=1, artist="A Band", title="One",
                      filename="01 - One.opus", provenance={}, state="done")
    plan = AlbumPlan(source_url="https://www.youtube.com/playlist?list=X", source_id="X",
                     kind=Kind.OFFICIAL_ALBUM, album="An Album", albumartist="A Band", year=None,
                     cover_url="https://i.ytimg.com/vi/aaaaaaaaaaa/maxresdefault.jpg",
                     folder="A Band/An Album", tracks=[track])
    save_plan(plan, album_dir)
    was = (album_dir / ".ytalbum.json").read_bytes()

    save_plan(load_plan(album_dir), album_dir)

    assert (album_dir / ".ytalbum.json").read_bytes() == was
    assert json.loads(was)["schema"] == 1


@pytest.mark.parametrize("schema,reads", [(1, True), (2, True), (3, False), (None, False)])
def test_which_schemas_this_version_reads(tmp_path, schema, reads):
    from noaap.models import AlbumPlan

    body = {"source_url": "u", "source_id": "s", "kind": "official_album", "album": "a",
            "albumartist": "b", "year": None, "cover_url": None, "folder": "b/a", "tracks": [],
            "schema": schema}

    if reads:
        assert AlbumPlan.from_dict(body).album == "a"
    else:
        with pytest.raises(ValueError, match="unsupported plan schema"):
            AlbumPlan.from_dict(body)


# -- the promise --------------------------------------------------------------------------------------


def test_a_library_that_is_copied_recognises_its_own_files(tmp_path):
    """The whole package. Measured on the real one first: **all 2000 refs of a copied collection
    resolved — into the original**, so the copy worked by reading somebody else's files."""
    import shutil

    from noaap.download import iter_plans

    here, there = tmp_path / "here", tmp_path / "there"
    an_album(here)
    shutil.copytree(here, there)

    for root in (here, there):
        for album_dir, plan in iter_plans(root):
            ref = Path(plan.tracks[0].video_id)
            assert ref.parent == album_dir, f"{root.name}: a ref pointing out of its own library"
            assert ref.is_file()


def test_a_library_that_is_copied_and_the_original_deleted_still_holds(tmp_path):
    import shutil

    from noaap.download import iter_plans

    here, there = tmp_path / "here", tmp_path / "there"
    an_album(here)
    shutil.copytree(here, there)
    shutil.rmtree(here)

    for album_dir, plan in iter_plans(there):
        assert Path(plan.tracks[0].video_id).is_file()


# -- and the bin ---------------------------------------------------------------------------------


def test_a_bin_entry_keeps_its_paths_the_way_a_plan_does(tmp_path):
    """R-207, ruling 5. An entry holds a whole track and a ranking, and either can carry a path
    into the album it came from."""
    from noaap.download import load_plan
    from noaap.recycle import bin_track, entries

    album_dir = an_album(tmp_path)
    plan = load_plan(album_dir)
    track = plan.tracks[0]
    audio = album_dir / track.filename

    bin_track(tmp_path, album_dir, plan, track, reason="tested",
              audio=audio, ranking={"chosen": {"ref": str(audio)}})

    written = json.loads(next(tmp_path.rglob("bin.json")).read_text())
    assert written["ranking"]["chosen"]["ref"] == "./01 - One.opus"
    assert written["track"]["video_id"] == "./01 - One.opus"
    assert written["folder"] == "A Band/An Album", "which is how it is read back"

    listed = entries(tmp_path)
    assert listed[0].data["ranking"]["chosen"]["ref"] == str(audio), "absolute again, in memory"


def test_a_bin_entry_survives_the_library_moving(tmp_path):
    import shutil

    from noaap.download import load_plan
    from noaap.recycle import bin_track, entries

    here = tmp_path / "here"
    album_dir = an_album(here)
    plan = load_plan(album_dir)
    bin_track(here, album_dir, plan, plan.tracks[0], reason="tested",
              audio=album_dir / plan.tracks[0].filename,
              ranking={"chosen": {"ref": str(album_dir / plan.tracks[0].filename)}})

    there = tmp_path / "there"
    shutil.copytree(here, there)
    shutil.rmtree(here)

    ref = entries(there)[0].data["ranking"]["chosen"]["ref"]
    assert ref.startswith(str(there)), "it names the album where the library is now"
    assert str(tmp_path / "here") not in ref


# -- two different absences ---------------------------------------------------------------------------


def test_an_album_whose_own_files_are_gone_is_named(tmp_path):
    from noaap.download import load_plan, lost_files

    album_dir = an_album(tmp_path)
    plan = load_plan(album_dir)
    assert lost_files(album_dir, plan) == []

    (album_dir / plan.tracks[0].filename).unlink()

    assert lost_files(album_dir, plan) == ["01 - One.opus"]


def test_a_source_that_is_gone_is_not_that(tmp_path):
    """R-207, ruling 3. An album taken in from a folder that is no longer mounted is **complete**:
    its files are here. Saying otherwise tells a user their library is broken when only a re-fetch
    would be."""
    from noaap.download import load_plan, lost_files, save_plan

    album_dir = an_album(tmp_path)
    plan = load_plan(album_dir)
    plan.source_url = plan.source_id = "/mnt/a-share-that-is-not-mounted/An Album"
    for track in plan.tracks:
        track.video_id = "/mnt/a-share-that-is-not-mounted/An Album/01 - One.opus"
    save_plan(plan, album_dir)

    assert lost_files(album_dir, load_plan(album_dir)) == []


def test_the_page_says_it_and_says_where(tmp_path):
    from noaap.config import Config
    from noaap.download import load_plan
    from noaap.web import App

    album_dir = an_album(tmp_path)
    (album_dir / load_plan(album_dir).tracks[0].filename).unlink()

    missing = App(Config(musicbrainz=False, lyrics=False), tmp_path).settings()["missing"]

    assert missing["albums"] == 1 and missing["tracks"] == 1
    assert missing["where"] == ["A Band/An Album"]


# -- a scope that matches nothing is a typo ------------------------------------------------------------


def a_library(tmp_path) -> Path:
    an_album(tmp_path, "An Album")
    return tmp_path


@pytest.mark.parametrize("args,says", [
    (["adopt", "--album", "No Such Album", "--rename", "--apply"], "matched no album"),
    (["adopt", "--only", "Nobody", "--undo"], "matched no adopted album"),
    (["adopt", "--album", "No Such Album"], "matched no folder"),
])
def test_a_scope_that_matches_nothing_is_said_and_is_not_a_success(tmp_path, args, says):
    """R-207, item f. "0 album(s): 0 file(s) renamed" and an exit of 0 is the same quiet wrong
    answer as telling somebody their mistyped source folder held no albums."""
    from test_watch import run_cli

    library = a_library(tmp_path / "library")
    code, said = run_cli([*args, "--library", str(library)], tmp_path / "cfg")

    assert code == 2 and says in said and "Traceback" not in said


def test_a_scope_that_matches_something_still_works(tmp_path):
    from test_watch import run_cli

    library = a_library(tmp_path / "library")

    code, said = run_cli(["adopt", "--album", "An Album", "--library", str(library)],
                         tmp_path / "cfg")

    assert code == 0, said
    assert "matched no" not in said, "the scope found its folder; what it then said about it is its own answer"
    assert "An Album" in said


# -- one rule, and one command that applies it to a whole library ------------------------------------


def old_style(album_dir: Path) -> dict:
    """The file as noaap 1.5.0 wrote it: every path absolute, schema 1."""
    from noaap.download import PLAN_FILE, load_plan

    plan = load_plan(album_dir)
    data = plan.to_dict()
    data["schema"] = 1
    (album_dir / PLAN_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


def test_a_plan_whose_paths_are_still_absolute_would_be_written_differently(tmp_path):
    """`rewritten` is the question repair asks of every album, and the only one it needs to."""
    from noaap.download import load_plan, rewritten, save_plan

    album_dir = an_album(tmp_path)
    old_style(album_dir)

    assert rewritten(album_dir, load_plan(album_dir)) is True
    save_plan(load_plan(album_dir), album_dir)
    assert rewritten(album_dir, load_plan(album_dir)) is False


def test_what_a_save_writes_is_decided_in_one_place(tmp_path):
    """Two callers ask the same function, because a second copy of the rule is a second thing to
    get wrong — which is how `plan --verify` first reported a conversion as a plan losing a field."""
    from noaap.download import PLAN_FILE, as_saved, load_plan, written

    album_dir = an_album(tmp_path)

    assert as_saved(written(load_plan(album_dir), album_dir)) == (album_dir / PLAN_FILE).read_text()


def test_verify_reports_the_conversion_and_counts_it_once(tmp_path, capsys):
    """R-207, ruling 2. The three counts are exclusive: a converted plan is not also byte-identical
    and not also a plan gaining defaults."""
    from noaap.cli import _verify_plans
    from noaap.config import Config

    album_dir = an_album(tmp_path)
    old_style(album_dir)

    assert _verify_plans(Config(), tmp_path) == 0
    said = capsys.readouterr().out
    assert "0 byte-identical" in said and "0 would gain default fields" in said
    assert "6 ref(s) in 1 plan(s) would become relative" in said
    assert "0 would lose or change something" in said


def test_repair_converts_a_whole_library(tmp_path):
    """R-207, ruling 2: one command, and every album in the library is written the new way."""
    from noaap.download import PLAN_FILE
    from noaap.service import Service

    for name in ("An Album", "Another Album"):
        old_style(an_album(tmp_path, name))

    service = Service(Config_of(tmp_path), tmp_path)
    service.repair(dry_run=True)
    assert all(json.loads((tmp_path / "A Band" / n / PLAN_FILE).read_text())["schema"] == 1
               for n in ("An Album", "Another Album")), "a dry run writes nothing"

    service.repair(dry_run=False)

    for name in ("An Album", "Another Album"):
        data = json.loads((tmp_path / "A Band" / name / PLAN_FILE).read_text())
        assert data["schema"] == 2 and data["source_id"] == "./"


def Config_of(root: Path):
    from noaap.config import Config

    cfg = Config()
    cfg.library_root = root
    return cfg
