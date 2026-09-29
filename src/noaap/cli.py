"""Command line: `noaap fetch|plan|download|update|search|serve|config`. A thin layer over service.py."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import config as config_mod
from .config import Config
from .download import (
    CANDIDATE_1_0,
    PLAN_FILE,
    as_saved,
    read_plan,
    widen_candidates,
    written,
)
from .models import AlbumPlan, PlanTrack, SourceRef, kept
from .service import Service, collection_address, exit_code
from .sources import NotSupported

PROV_MARK = {"mb": "MB", "yt_music": "YTM", "yt_title": "title", "playlist": "playlist",
             "user": "user", "file_tags": "tags", "folder_name": "folder", "file_name": "name"}
BLOCKED = 3  # exit code: YouTube is refusing requests right now; stop asking


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="noaap", description="Turn YouTube playlists into tagged albums.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="plan and download a playlist, video or channel URL")
    f.add_argument("url")
    f.add_argument("--library", type=Path, help="library root (overrides the config file)")
    f.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    f.add_argument("--all", action="store_true", help="channel: take every release and playlist")
    f.add_argument("--pick", metavar="SPEC", help="channel: which ones, e.g. 1,3-5 (default: ask)")
    f.add_argument("--no-mb", action="store_true", help="skip the MusicBrainz lookup")
    f.add_argument("--no-lyrics", action="store_true", help="do not look lyrics up at lrclib.net")
    f.add_argument("--dump-collection", type=Path, metavar="FILE", help="also save what YouTube returned (for test fixtures)")

    se = sub.add_parser("search", help="find an artist's albums on YouTube and pick which to fetch")
    se.add_argument("artist")
    se.add_argument("--library", type=Path)
    se.add_argument("--dry-run", action="store_true", help="print the plans, write nothing")
    se.add_argument("--all", action="store_true", help="take everything found")
    se.add_argument("--pick", metavar="SPEC", help="which ones, e.g. 1,3-5 (default: ask)")
    se.add_argument("--no-mb", action="store_true", help="skip MusicBrainz (lookups and the discography check)")
    se.add_argument("--no-lyrics", action="store_true", help="do not look lyrics up at lrclib.net")

    pl = sub.add_parser("plan", help="write the plan into the album folder for editing, download nothing")
    pl.add_argument("url", nargs="?")
    pl.add_argument("--library", type=Path)
    pl.add_argument("--no-mb", action="store_true", help="skip the MusicBrainz lookup")
    pl.add_argument("--verify", action="store_true",
                    help="read every plan in the library and report anything a rewrite would lose; writes nothing")

    d = sub.add_parser("download", help="download from an (edited) plan in an album folder")
    d.add_argument("album_dir", type=Path)
    d.add_argument("--no-lyrics", action="store_true", help="do not look lyrics up at lrclib.net")

    pr = sub.add_parser("prune", help="delete the tracks that are no longer in the source playlist")
    pr.add_argument("album_dir", type=Path)
    pr.add_argument("--yes", action="store_true", help="do not ask")

    rp = sub.add_parser("repair", help="tidy artist names and measure files in the library, offline")
    rp.add_argument("--dry-run", action="store_true", help="say what it would do, write nothing")
    rp.add_argument("--library", type=Path)
    rp.add_argument("--strays", action="store_true",
                    help="also look for files noaap 1.4.0/1.5.0 copied into an adopted album's root "
                         "(reports only; add --apply to move them to the bin)")
    rp.add_argument("--find-moved", action="store_true",
                    help="find a track's file again by what it holds, where it was renamed or moved "
                         "(reports only; add --apply to write it into the plan)")
    rp.add_argument("--under", type=Path, metavar="FOLDER",
                    help="with --find-moved: also look under FOLDER for the source folder an album was "
                         "taken in from, when that folder has moved")
    rp.add_argument("--apply", action="store_true",
                    help="with --strays or --find-moved: actually do it")

    ly = sub.add_parser("lyrics", help="fetch lyrics for tracks that have none yet (.lrc beside the file + tag)")
    ly.add_argument("--library", type=Path)
    ly.add_argument("--artist", help="only this album artist")
    ly.add_argument("--refetch", action="store_true", help="look every track up again (keeps lyrics you wrote yourself)")
    ly.add_argument("--near", action="store_true",
                    help="afterwards, align lrclib's near misses to decide whether they are this recording's words")
    ly.add_argument("--dry-run", action="store_true", help="with --near: look up only, align nothing, write nothing")

    dl = sub.add_parser("delete", help="delete an album (or one track) — files are removed")
    dl.add_argument("album_dir", type=Path)
    dl.add_argument("--track", metavar="VIDEO_ID", help="delete only this track")
    dl.add_argument("--yes", action="store_true", help="do not ask")

    u = sub.add_parser("update", help="re-check every album in the library against its source")
    u.add_argument("--library", type=Path)
    u.add_argument("--dry-run", action="store_true", help="only report what changed")
    u.add_argument("--no-mb", action="store_true", help="skip the MusicBrainz lookup")
    u.add_argument("--no-lyrics", action="store_true", help="do not look lyrics up at lrclib.net")
    u.add_argument("--deep", action="store_true", help="read every album fully, even unchanged ones")

    sv = sub.add_parser("serve", help="web UI for the library (also installable as an app)")
    sv.add_argument("--library", type=Path)
    sv.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to reach it from other devices (no login!)")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--idle-exit", type=float, default=0, metavar="SECONDS", help="stop after this long without requests or jobs (for socket activation)")

    wu = sub.add_parser("watch-service", help="the watcher as its own systemd user service")
    wu.add_argument("action", choices=["install", "uninstall", "status"])
    wu.add_argument("--port", type=int, default=None, help="the port the app listens on")

    tm = sub.add_parser("timing-serve", help="run the local aligner as a small HTTP service for another machine")
    tm.add_argument("--host", default="0.0.0.0", help="0.0.0.0 by default: the point is to be reached from the LAN")
    tm.add_argument("--port", type=int, default=8770)
    tm.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")

    sd = sub.add_parser("service", help="run the web UI on demand via systemd (user level)")
    sd.add_argument("action", choices=("install", "uninstall", "status", "restart"))
    sd.add_argument("--force", action="store_true", help="restart even while a job is running")
    sd.add_argument("--port", type=int, default=None, help="install: the port to listen on (default 8765)")
    sd.add_argument("--idle-exit", type=int, default=None, metavar="SECONDS",
                    help="install: stop the service after this long without requests or jobs (default 900)")

    ap = sub.add_parser("app", help="desktop launcher with its own window, not another browser window")
    ap.add_argument("action", choices=("install", "uninstall", "status"))
    ap.add_argument("--port", type=int, default=None, help="port of the web UI (default: the installed service's)")
    ap.add_argument("--browser", help="which Chromium-based browser to use")
    ap.add_argument("--remove-profile", action="store_true", help="uninstall: also delete the app's browser profile")

    rc = sub.add_parser("recycle", help="what noaap moved aside instead of deleting")
    rc.add_argument("action", choices=["list", "restore", "empty"])
    rc.add_argument("entry", nargs="?", help="restore: the entry id, or enough of it to be unique")
    rc.add_argument("--library", type=Path)
    rc.add_argument("--older-than", type=float, metavar="DAYS",
                    help="empty: only entries older than this many days")

    mr = sub.add_parser("merge", help="take the better copies out of another library (shows first)")
    mr.add_argument("source", help="the library to take from — it is never written to")
    mr.add_argument("--library", type=Path, help="the library to merge into (overrides the config)")
    mr.add_argument("--apply", action="store_true", help="actually do it (without this: a dry run)")
    mr.add_argument("--undecided", action="store_true", help="list only what it will not decide")
    mr.add_argument("--new", action="store_true",
                    help="also fetch the albums this library does not have at all")
    mr.add_argument("--only", metavar="ARTIST", help="one artist's albums, on both sides")
    mr.add_argument("--album", metavar="NAME",
                    help="one album in the library being changed (the other side keeps its own names)")

    ad = sub.add_parser("adopt", help="take a collection in where it stands: one plan per album, nothing else")
    ad.add_argument("root", nargs="?", help="the folder to adopt — the library root by default")
    ad.add_argument("--library", type=Path, help="the library this becomes (defaults to the config's)")
    ad.add_argument("--apply", action="store_true", help="actually write the plans (without this: a dry run)")
    ad.add_argument("--only", metavar="ARTIST", help="one artist's folder")
    ad.add_argument("--album", metavar="NAME", help="one album folder")
    ad.add_argument("--undo", action="store_true",
                    help="give the albums back: their names, their tags, and nothing of noaap's left")
    ad.add_argument("--rename", action="store_true",
                    help="afterwards: give the files noaap's names (the folder stays where it is)")
    ad.add_argument("--retag", action="store_true",
                    help="afterwards: write the plan's tags in, keeping every field noaap does not model")

    wa = sub.add_parser("watch", help="watch the configured folders and hand what arrives to the app")
    wa.add_argument("--once", action="store_true", help="one look at each folder, then stop")
    wa.add_argument("--interval", type=float, default=None, metavar="SECONDS", help="how often to look")
    wa.add_argument("--settle", type=float, default=None, metavar="SECONDS",
                    help="how long a folder must sit still before it counts as arrived")
    wa.add_argument("--port", type=int, default=8765, help="the port the app listens on")

    mg = sub.add_parser("migrate", help="take over what ytalbum left on this machine (shows first)")
    mg.add_argument("--apply", action="store_true", help="actually do it (without this: a dry run)")
    mg.add_argument("--uninstall-old", action="store_true",
                    help="also remove ytalbum's systemd units and desktop launcher (never its browser profile)")

    c = sub.add_parser("config", help="show or set configuration")
    c.add_argument("--library", type=Path,
                   help="SET the library root (on every other command --library only overrides that "
                        "run); with no setter, `config` reports and writes nothing")
    c.add_argument("--cookies-from-browser", metavar="BROWSER[:PROFILE]", help="use a browser's YouTube login: gets past the bot check and unlocks age-restricted videos; 'none' to unset")
    c.add_argument("--cookies-file", type=Path, metavar="FILE", help="use an exported cookies.txt instead; 'none' to unset")
    c.add_argument("--lyrics", choices=("on", "off"), help="look lyrics up at lrclib.net when downloading")

    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # a captured stream in a test is not a real one
        sys.stdout.reconfigure(line_buffering=True)  # keep progress in order with stderr when piped
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if notice := config_mod.legacy_notice():  # still configured as ytalbum (§9, slice 52)
        print(notice, file=sys.stderr)
    cfg = config_mod.load()
    if getattr(args, "no_mb", False):
        cfg.musicbrainz = False
    if getattr(args, "no_lyrics", False):
        cfg.lyrics = False

    try:
        match args.cmd:
            case "config":
                return _config(args, cfg)
            case "recycle":
                return _recycle(args, cfg)
            case "plan" if args.verify:
                return _verify_plans(cfg, getattr(args, "library", None))
            case "fetch" | "plan":
                if not args.url:
                    print("give a URL, or --verify to check the plans already here", file=sys.stderr)
                    return 2
                return _fetch(args, cfg)
            case "search":
                return _search(args, cfg)
            case "download":
                return exit_code(_service(cfg, _library(args, cfg, required=False)).download_existing(args.album_dir))
            case "update":
                library = _library(args, cfg, required=True)
                if library is None:
                    return 2
                code = exit_code(_service(cfg, library).update_all(report_only=args.dry_run, deep=args.deep))
                _say_lost(library)
                return code
            case "serve":
                return _serve(args, cfg)
            case "timing-serve":
                from .timing_serve import serve as timing_serve

                return timing_serve(host=args.host, port=args.port, device=args.device)
            case "prune":
                return _prune(args, cfg)
            case "service":
                return _systemd(args, cfg)
            case "app":
                return _app(args)
            case "merge":
                library = _library(args, cfg, required=True)
                if library is None:
                    return 2
                from . import merge as merge_pass
                from .ranking import Verdict
                _scope_matched(args, None, "")  # checked after the survey, which is where it is known
                source_dir = Path(args.source).expanduser()
                if not source_dir.is_dir():
                    # it answered "0 albums, nothing to do" for a path that was simply not there,
                    # which tells a user who mistyped it that everything is fine
                    raise config_mod.Refused(f"there is no folder at {source_dir}")
                found = merge_pass.survey(source_dir, library, log=print,
                                          artist=args.only, album=args.album)
                _scope_matched(args, len(found.pairing.pairs) + len(found.pairing.unpaired), "track")
                wanted = [Verdict.UNDECIDED] if args.undecided else None
                for line in merge_pass.report(found, verdicts=wanted, applying=args.apply):
                    print(line)
                if args.apply:
                    done = merge_pass.carry_out(found, library, log=print)
                    print(f"{done['replaced']} replaced, {done['filled']} filled, "
                          f"{done['offered']} listed for you to decide"
                          + (f", {done['failed']} could not be taken" if done["failed"] else ""))
                if args.new:
                    service = _service(cfg, library)
                    print("albums this library does not have:")
                    if not args.apply:
                        for album_dir, tracks in sorted(merge_pass.unpaired_albums(found).items()):
                            plan = tracks[0].plan
                            print(f"  {plan.albumartist} — {plan.album} ({len(tracks)} track(s))")
                        print("nothing was fetched. `--new --apply` does it.")
                    else:
                        got = merge_pass.take_new(found, lambda url: service.fetch(url), log=print)
                        print(f"{got['taken']} album(s) fetched, {got['held']} already here and left alone")
                return 0
            case "adopt":
                library = _library(args, cfg, required=True)
                if library is None:
                    return 2
                from . import adopt as adopt_pass
                from . import sources
                root = Path(args.root).expanduser() if args.root else library
                if not root.is_dir():
                    print(f"not a folder: {root}", file=sys.stderr)
                    return 2
                source = sources.get("folder", cfg)
                if args.undo:
                    return _adopt_undo(adopt_pass, library, root, args)
                if args.rename or args.retag:
                    return _adopt_promote(adopt_pass, root, args)
                found = adopt_pass.survey(root, library, source, artist=args.only,
                                          album=args.album, log=print)
                _scope_matched(args, len(found.adoptions), "folder")
                for line in adopt_pass.report(found, applying=args.apply):
                    print(line)
                if args.apply:
                    done = adopt_pass.carry_out(found, log=print)
                    print(f"{done['adopted']} album(s) adopted, {done['tracks']} track(s)")
                    _say_lost(library)
                return 0
            case "watch":
                return _watch(args, cfg)
            case "watch-service":
                from . import systemd

                if args.action == "status":
                    print(f"watcher: {systemd.watching()}")
                    return 0
                doing = systemd.install_watch(cfg, args.port) if args.action == "install" \
                    else systemd.uninstall_watch()
                for line in doing:
                    print(line)
                if args.action == "install":
                    print("the watcher is running. `systemctl --user stop noaap-watch` stops it.")
                return 0
            case "migrate":
                from . import migrate
                for line in migrate.run(apply=args.apply, uninstall_old=args.uninstall_old):
                    print(line)
                return 0
            case "delete":
                return _delete(args, cfg)
            case "repair":
                library = _library(args, cfg, required=True)
                if library is None:
                    return 2
                code = exit_code(_service(cfg, library).repair(dry_run=args.dry_run,
                                                                strays=args.strays, apply=args.apply,
                                                                find_moved=args.find_moved,
                                                                under=args.under))
                _say_lost(library)
                return code
            case "lyrics":
                library = _library(args, cfg, required=True)
                if library is None:
                    return 2
                service = _service(cfg, library)
                outcomes = service.fetch_lyrics(refetch=args.refetch, artist=args.artist)
                if args.near:
                    outcomes += service.check_near_lyrics_all(refetch=args.refetch, artist=args.artist,
                                                              dry_run=args.dry_run)
                return exit_code(outcomes)
    except NotSupported as e:
        print(f"not supported: {e}", file=sys.stderr)
        return 2
    except config_mod.Refused as e:
        # a wrong setting or a wrong argument is answered in one sentence, never with a stack
        # trace: a traceback for "nothing is watched yet" hides the only line worth reading
        # (found by the P55 acceptance run, §9, slice 59)
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted — run the same command again to resume", file=sys.stderr)
        return 130
    return 0


# -- commands ----------------------------------------------------------------------------


def _service(cfg: config_mod.Config, library: Path | None) -> Service:
    if not cfg.resolved_js_runtime():
        print("warning: no JavaScript runtime found (deno/node/bun/quickjs) — YouTube may hide formats or fail; see DESIGN.md §3.5", file=sys.stderr)
    return Service(cfg, library, log=lambda s: print(s, file=sys.stderr), on_plan=_print_plan, on_track=_print_track)


def _scope_matched(args: argparse.Namespace, found: int | None, what: str) -> None:
    """A `--only` or `--album` that matched nothing is a typo, not a success.

    "0 album(s): 0 file(s) renamed" and an exit of 0 is the same quiet wrong answer as telling
    somebody their mistyped source folder held no albums (R-207, item f).
    """
    if found is None or found:
        return
    named = " and ".join(filter(None, [f"--only {args.only!r}" if getattr(args, "only", None) else "",
                                       f"--album {args.album!r}" if getattr(args, "album", None) else ""]))
    if named:
        raise config_mod.Refused(f"{named} matched no {what} — nothing was done")


def _say_lost(library: Path | None) -> None:
    """What a pass over the library ends with, when the library has something to say (R-212).

    Nothing at all when nothing is lost, which is every healthy library: a line printed every time is
    a line nobody reads, and this one has already gone unread once.
    """
    from .download import lost_albums, lost_sentence

    if library and library.is_dir() and (line := lost_sentence(lost_albums(library))):
        print(line)


def _print_lost(cfg: config_mod.Config) -> None:
    """Albums whose own files are not where their plan says.

    The one line worth printing when a library has been moved and not yet told about it — and
    deliberately not the same question as "the folder this was taken in from is gone", which is a
    fact about a source and only matters where a re-fetch is asked for (R-207, ruling 3).
    """
    from .download import lost_albums, lost_sentence

    root = cfg.library_root
    if not root or not root.is_dir():
        return
    albums = lost_albums(root)
    if not (line := lost_sentence(albums)):
        return
    print(f"missing:      {line}")
    for album_dir, lost in albums[:3]:
        print(f"              {album_dir.relative_to(root)} ({len(lost)})")
    if len(albums) > 3:
        print(f"              …and {len(albums) - 3} more")
    print("              if the library was moved, point noaap at it: noaap config --library PATH")


def _print_watches(cfg: config_mod.Config) -> None:
    """What is watched, and why a setting cannot stand (R-200, ruling 4: it says so here)."""
    if not cfg.watches:
        print("watching:     (nothing — `noaap watch --help`)")
        return
    from . import watch as watch_pass

    state = watch_pass.read_state()
    for row in cfg.watches:
        when = state.get(row.name, {}).get("looked")
        print(f"watching:     {row.name} ({row.shape}) {row.folder}"
              + (f" — last looked {when}" if when else " — not looked at yet")
              + ("" if row.folder.is_dir() else "  [the folder is not there]"))
    for why in config_mod.watch_trouble(cfg.watches, cfg.library_root):
        print(f"  refused:    {why}", file=sys.stderr)


def _config(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    """Show the configuration, or change it — and **say so when it changes** (§9, slice 67, R-229).

    `--library` means two different things on two commands: everywhere else it overrides the library for
    that one run and writes nothing, here it *sets* it. Somebody can fall into that — somebody did, using
    `config --library PATH` as a report and rewriting the library root of a real installation each time.
    So a setter now names the file it wrote and prints what changed, from what, to what; and a `config`
    with no setter writes nothing at all, which a case proves by hashing the file.
    """
    changes = {}
    if args.library:
        changes["library_root"] = str(args.library.expanduser().resolve())
    if args.cookies_from_browser:
        changes["cookies_from_browser"] = None if args.cookies_from_browser == "none" else args.cookies_from_browser
    if args.cookies_file:
        changes["cookies_file"] = None if str(args.cookies_file) == "none" else str(args.cookies_file.expanduser().resolve())
    if args.lyrics:
        changes["lyrics"] = args.lyrics == "on"
    if changes:
        was = {name: getattr(cfg, name, None) for name in changes}
        written = None
        for name, value in changes.items():
            written = config_mod.save_setting(name, value)
        print(f"wrote {written}")
        for name, value in changes.items():
            before = was[name]
            print(f"  {name}: {before if before is not None else '(not set)'} → "
                  f"{value if value is not None else '(not set)'}")
        cfg = config_mod.load()
    runtime = cfg.resolved_js_runtime()
    # the one it read, which is not always the one it writes (§9, slice 52)
    print(f"config file:  {config_mod.read_path()}")
    print(f"library_root: {cfg.library_root or '(not set)'}")
    print(f"cookies:      {cfg.cookies_file or cfg.cookies_from_browser or '(none — age-restricted videos are skipped)'}")
    print(f"musicbrainz:  {'on' if cfg.musicbrainz else 'off'}")
    _print_watches(cfg)
    _print_lost(cfg)
    print(f"lyrics:       {'on (lrclib.net)' if cfg.lyrics else 'off'}")
    pot = cfg.resolved_pot_provider()
    if not pot or cfg.pot_mode == "off":
        print(f"po tokens:    {'off' if cfg.pot_mode == 'off' else '(no generator — some streams may be withheld; see README)'}")
    else:
        from .pot import ping

        running = ping(cfg.pot_port)
        mode = f"server on 127.0.0.1:{cfg.pot_port}, stops after {cfg.pot_idle}s idle" if cfg.pot_mode == "server" else "script"
        state = f" — running (v{running['version']})" if running else (" — started on demand" if cfg.pot_mode == "server" else "")
        print(f"po tokens:    {mode}{state}\n              {pot}")
    print(f"js runtime:   {' '.join(filter(None, runtime)) if runtime else 'NONE FOUND — install deno or node'}")
    # reported, not enforced: a library can be browsed, tagged, searched and have its lyrics fetched
    # with no ffmpeg at all. It is downloading and trimming that stop, and they stop at the moment
    # of use, which is a bad moment to find out (docs/qa-catalog.md, AP).
    if cfg.library_root and cfg.library_root.is_dir():
        from .recycle import total as bin_total

        count, size = bin_total(cfg.library_root)
        print(f"recycle bin:  {count} entr{'y' if count == 1 else 'ies'}, {size / 1e6:.1f} MB"
              + ("" if not count else " — `noaap recycle list`"))
    ffmpeg = cfg.resolved_ffmpeg()
    print(f"ffmpeg:       {ffmpeg or 'NOT FOUND — downloading and trimming will fail; apt install ffmpeg'}")
    return 0


def _recycle(args: argparse.Namespace, cfg: Config) -> int:
    """The bin: what is in it, putting one back, and emptying it — never automatically.

    `recycle list | head` closes the pipe under us, which Python reports as a BrokenPipeError with a
    traceback on the way out. A listing command has to survive being piped into `head`.
    """
    from . import recycle as bin_

    root = _library(args, cfg, required=True)
    if not root or not root.is_dir():
        return 2
    if args.action == "list":
        return _recycle_list(bin_, root)
    if args.action == "restore":
        if not args.entry:
            print("which one? `noaap recycle list` shows the ids", file=sys.stderr)
            return 2
        outcome = _service(cfg, root).restore(args.entry)
        if outcome.message:
            print(outcome.message, file=sys.stderr if outcome.status == "failed" else sys.stdout)
        return exit_code(outcome)
    gone, freed = bin_.empty(root, args.older_than)
    print(f"removed {gone} entr{'y' if gone == 1 else 'ies'} for good, {freed / 1e6:.1f} MB")
    return 0


def _recycle_list(bin_: Any, root: Path) -> int:
    try:
        found = bin_.entries(root)
        if not found:
            print("the recycle bin is empty")
            return 0
        for entry in found:
            print(f"{entry.id}  {entry.bytes / 1e6:6.1f} MB  {entry.describe()}")
        count, size = bin_.total(root)
        print(f"\n{count} entr{'y' if count == 1 else 'ies'}, {size / 1e6:.1f} MB — "
              f"nothing here is ever removed on its own")
    except BrokenPipeError:
        # `| head` closed the pipe. Point stdout at nothing so the interpreter's exit-time flush has
        # somewhere to go; closing it instead would be correct here and break anything capturing it.
        with contextlib.suppress(OSError, ValueError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


def _verify_plans(cfg: Config, library: Path | None) -> int:
    """Read every plan and re-serialise it in memory: what would a rewrite lose? (DESIGN.md §6)

    Nothing is written. Additions are expected and not faults — a plan saved before a field existed
    gains it with its default — so only two things count: a key that disappears, or a value that
    changes. Unknown keys are listed because they say a newer noaap has been here.
    """
    root = library or cfg.library_root
    if not root or not root.is_dir():
        print("set the library first: noaap config --library PATH", file=sys.stderr)
        return 2
    paths = sorted(root.glob(f"*/*/{PLAN_FILE}"))
    identical = filled = converted = reshapes = 0
    faults: list[str] = []
    moved: list[str] = []
    unknown: dict[str, int] = {}
    for path in paths:
        where = f"{path.parent.parent.name}/{path.parent.name}"
        try:
            raw = path.read_text(encoding="utf-8")
            before = json.loads(raw)
            # **built the way a load builds it**, through the one place that decides that (§9, slice 62).
            # Parsing the raw file instead left a candidate's newer fields in the track's carried-through
            # keys rather than on the candidate, so this said 116 real plans would lose a `stream_sha`
            # that a save puts back exactly where it found it.
            plan = read_plan(path, path.parent)
        except (OSError, ValueError, TypeError) as e:
            faults.append(f"{where}: cannot be read — {e}")
            continue
        # what a *save* would write, which is not `to_dict()`: a path inside the album is written
        # relative to it, and that rewrite is exactly what this has to be able to report (§9, slice 60)
        after = written(plan, path.parent)
        # **compared as memory holds them**, because that is what the promise is about (§9, slice 62).
        # A field written beside its candidate instead of inside it has not been lost, and one left out
        # because it still has its default says nothing that was not already said. Both sides go
        # through a load, so what is compared is what the two files *mean*: a value that really
        # disappears still shows, as that value turning into its default.
        gone, changed = _plan_differences(_as_memory(before), _as_memory(after))
        # the direct question, and not "would narrowing change it": a candidate holding a field 0.9.1
        # does not know is what makes a plan unreadable to it (§9, slice 62)
        reshaped = any(set(copy) - set(CANDIDATE_1_0)
                       for track in (before.get("tracks") or [])
                       for copy in (track.get("candidates") or []))
        for key in _plan_unknown(plan):
            unknown[key] = unknown.get(key, 0) + 1
        refs = [c for c in changed if _is_conversion(c[1], c[2], path.parent, root)]
        # the schema reads 2 *because* the plan now holds a relative ref, so it is part of the same
        # conversion and not a value that changed behind the owner's back (§9, slice 60)
        bump = [c for c in changed if c[0] == "schema"] if refs else []
        changed = [c for c in changed if c not in refs and c not in bump]
        converted += len(refs)
        reshapes += bool(reshaped)
        if refs and not (gone or changed):
            moved.append(f"{where}: {len(refs)} ref(s) would become relative to the library")
        if gone or changed:
            faults.append(f"{where}: would lose {gone}" if gone
                          else f"{where}: would change {[f'{k}: {b!r} -> {a!r}' for k, b, a in changed[:3]]}")
        elif not refs and not reshaped:
            # **the three counts are exclusive.** A converted plan is neither byte-identical nor a
            # plan gaining defaults, and counting it twice would make the totals lie (§9, slice 60).
            if as_saved(after) == raw:
                identical += 1
            else:
                filled += 1
    print(f"{len(paths)} plan(s): {identical} byte-identical, {filled} would gain default fields, "
          + (f"{converted} ref(s) in {len(moved)} plan(s) would become relative, " if converted else "")
          + (f"{reshapes} plan(s) would have their copies written in the shape ytalbum 0.9.1 reads, "
             if reshapes else "")
          + f"{len(faults)} would lose or change something")
    for key, n in sorted(unknown.items()):
        print(f"  unknown field {key!r} on {n} plan(s) — written by a newer noaap, carried through")
    for line in moved[:5]:
        print(f"  {line}")
    if len(moved) > 5:
        print(f"  …and {len(moved) - 5} more")
    for fault in faults:
        print(f"  {fault}")
    return 1 if faults else 0


def _plan_differences(before: Any, after: Any, path: str = "") -> tuple[list[str], list[tuple[str, Any, Any]]]:
    """(keys that disappeared, values that changed). A change carries both values, because whether
    it is a fault or a conversion can only be told by looking at them (§9, slice 60)."""
    gone: list[str] = []
    changed: list[tuple[str, Any, Any]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        gone += [f"{path}{k}" for k in before.keys() - after.keys()]
        for k in before.keys() & after.keys():
            g, c = _plan_differences(before[k], after[k], f"{path}{k}.")
            gone += g
            changed += c
    elif isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            changed.append((f"{path}length", len(before), len(after)))
        for i, (b, a) in enumerate(zip(before, after, strict=False)):
            g, c = _plan_differences(b, a, f"{path}{i}.")
            gone += g
            changed += c
    elif before != after:
        changed.append((path.rstrip("."), before, after))
    return gone, changed


def _as_memory(data: dict[str, Any]) -> dict[str, Any]:
    """What this file means once it is read: defaults filled in, candidates whole (§9, slice 62)."""
    return AlbumPlan.from_dict(widen_candidates(deepcopy(data))).to_dict()


def _is_conversion(before: Any, after: Any, album_dir: Path, library: Path) -> bool:
    """Is this change **exactly** the rewrite of an absolute path into a relative one?

    Nothing else may hide behind it. The value must be a path that was absolute, is now relative,
    and names the same file under this album or this library — anything else is a change a person
    has to be told about (§9, slice 60).
    """
    if not isinstance(before, str) or not isinstance(after, str):
        return False
    if not before.startswith("/") or after.startswith("/") or not after:
        return False
    return any((root / after).as_posix() == before for root in (album_dir, library))


def _watch(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    """Look at the configured folders, and hand what has arrived to the app. Does no work itself."""
    import json
    import time
    import urllib.error
    import urllib.request

    from . import watch as watch_pass
    from .sources_folder import AUDIO

    if not cfg.watches:
        print("nothing is watched. Put a [[watch]] table in the config file; `noaap config` shows it.",
              file=sys.stderr)
        return 2
    if trouble := config_mod.watch_trouble(cfg.watches, cfg.library_root):
        for why in trouble:
            print(f"refused: {why}", file=sys.stderr)
        return 2

    settle = args.settle if args.settle is not None else watch_pass.SETTLE
    interval = args.interval if args.interval is not None else watch_pass.INTERVAL
    here = watch_pass.runs(cfg)
    for run in here:
        run.watcher.settle = settle

    def ask(name: str, album: str) -> bool:
        """Ask the app to take it. This is what wakes a socket-activated service."""
        body = json.dumps({"watch": name, "album": album}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{args.port}/api/arrived", data=body, method="POST",
            headers={"Content-Type": "application/json", "X-Noaap": "1"})
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                print(f"{name}: {album} — {json.load(answer)['job']['label']}", flush=True)
                return True
        except urllib.error.HTTPError as e:
            print(f"{name}: {album} — refused: {e.read().decode('utf-8', 'replace')[:200]}",
                  file=sys.stderr, flush=True)
        except OSError as e:
            print(f"{name}: {album} — the app did not answer ({e})", file=sys.stderr, flush=True)
        return False

    print(f"watching {len(here)} folder(s), looking every {interval:.0f}s, "
          f"settling after {settle:.0f}s" + (" (once)" if args.once else ""), flush=True)
    while True:
        now = time.monotonic()
        for run in here:
            watch_pass.once(run, AUDIO, now, ask, log=lambda s: print(s, flush=True))
        watch_pass.write_state(watch_pass.keep(here, time.strftime("%Y-%m-%d %H:%M")))
        if args.once:
            return 0
        time.sleep(interval)


def _adopt_undo(adopt_pass, library: Path, root: Path, args: argparse.Namespace) -> int:
    """Give back every adopted album under `root`, or say what it would give back."""
    from .download import iter_plans

    totals: dict[str, int] = {}
    albums = 0
    trouble: list[str] = []
    for album_dir, plan in iter_plans(root):
        if not plan.adopted or not adopt_pass.in_scope(album_dir, root, args.only, args.album):
            continue
        albums += 1
        if not args.apply:
            print(f"  would give back {plan.albumartist} — {plan.album} ({len(plan.tracks)} track(s))")
            continue
        print(f"  {plan.albumartist} — {plan.album}")
        # **one album's trouble is not the pass's.** A crash in the middle used to end the run with
        # most of the library still adopted and the renamed albums with no way back (found
        # 2026-09-28). Whatever fails is named at the end and the exit code says so.
        try:
            done = adopt_pass.give_back(album_dir, plan, library, log=print)
        except Exception as e:
            trouble.append(f"{plan.albumartist} — {plan.album}: {e}")
            print(f"  could not give it back: {e}", file=sys.stderr)
            continue
        if done.get("failed"):
            trouble.append(f"{plan.albumartist} — {plan.album}: {done['failed']} track(s) — "
                           "its plan was kept, run the undo again")
        for key, value in done.items():
            totals[key] = totals.get(key, 0) + value
    _scope_matched(args, albums, "adopted album")
    if not args.apply:
        print(f"{albums} adopted album(s). Nothing was changed. `--undo --apply` does it.")
        return 0
    print(f"{albums} album(s) given back: " + ", ".join(f"{v} {k}" for k, v in sorted(totals.items())))
    if totals.get("kept"):
        # said as a sentence and not only as one number among seven (R-223, ruling 3): an album adopted
        # before slice 65 has no record of what was in its folder, so this is what could not be decided.
        print(f"{totals['kept']} file(s) left where they are: nothing proves noaap wrote them. An album "
              "adopted by an earlier version has no record of what was in its folder.")
    if totals.get("binned"):
        print(f"{totals['binned']} cover file(s) moved to the bin rather than deleted: noaap wrote "
              "them, but only the weaker proof says so. `noaap recycle` lists them.")
    if trouble:
        print(f"\n{len(trouble)} album(s) not fully given back:", file=sys.stderr)
        for line in trouble:
            print(f"  {line}", file=sys.stderr)
        print("run the same command again to finish them.", file=sys.stderr)
        return 1
    return 0


def _adopt_promote(adopt_pass, root: Path, args: argparse.Namespace) -> int:
    """`--rename` / `--retag`: the two things a person may ask for after an adoption."""
    from .download import iter_plans, save_plan

    renamed = retagged = albums = refused = 0
    for album_dir, plan in iter_plans(root):
        if not plan.adopted or not adopt_pass.in_scope(album_dir, root, args.only, args.album):
            continue
        if why := adopt_pass.undo_data_complete(plan):
            print(f"  refused {plan.albumartist} — {plan.album}: {why}", file=sys.stderr)
            refused += 1
            continue
        albums += 1
        if not args.apply:
            print(f"  would touch {plan.albumartist} — {plan.album} ({len(plan.tracks)} track(s))")
            continue
        if args.rename:
            renamed += adopt_pass.rename(album_dir, plan, log=print)
        if args.retag:
            retagged += adopt_pass.retag(album_dir, plan, log=print)
        save_plan(plan, album_dir)
    _scope_matched(args, albums + refused, "album")
    if not args.apply:
        print(f"{albums} album(s), {refused} refused. Nothing was changed. `--apply` does it.")
        return 0
    print(f"{albums} album(s): {renamed} file(s) renamed, {retagged} file(s) retagged"
          + (f", {refused} refused" if refused else ""))
    return 0


def _plan_unknown(plan: AlbumPlan) -> list[str]:
    keys = list(kept(plan))
    for track in plan.tracks:
        keys += [f"tracks.{k}" for k in kept(track)]
    return sorted(set(keys))


def _fetch(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    dry = getattr(args, "dry_run", False)
    library = _library(args, cfg, required=not dry)
    if library is None and not dry:
        return 2
    service = _service(cfg, library)

    if not collection_address(args.url, cfg):
        outcome = service.fetch(args.url, dry=dry, plan_only=args.cmd == "plan", dump=getattr(args, "dump_collection", None))
        if outcome.status == "planned":
            print(f"\nplan written to {outcome.album_dir}/.ytalbum.json\nedit it, then run: noaap download '{outcome.album_dir}'")
        return exit_code(outcome)

    if args.cmd == "plan":
        print("`plan` takes one playlist; for a channel use `fetch --pick N --dry-run` first", file=sys.stderr)
        return 2
    groups = service.channel(args.url)
    if not groups:
        print("this channel has no releases or playlists", file=sys.stderr)
        return 1
    return _pick_and_fetch(service, groups, args, dry)


def _search(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    library = _library(args, cfg, required=not args.dry_run)
    if library is None and not args.dry_run:
        return 2
    service = _service(cfg, library)
    result = service.search(args.artist)
    if not result.refs:
        print("nothing found", file=sys.stderr)
        return 1
    if result.channel_url:
        print(f"artist channel: {result.channel_url}", file=sys.stderr)
    return _pick_and_fetch(service, result.groups, args, args.dry_run, missing=result.missing)


def _pick_and_fetch(service: Service, groups, args, dry: bool, missing: list[str] = ()) -> int:
    refs = [r for _, group in groups for r in group]
    _print_groups(groups, service.library_source_ids())
    if missing:
        print(f"\nMusicBrainz lists {len(missing)} more studio album(s) not found on YouTube: " + "; ".join(missing))
    chosen = _choose(refs, args)
    return exit_code(service.fetch_many(chosen, dry=dry)) if chosen else 0


def _prune(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    from .download import load_plan

    plan = load_plan(args.album_dir)
    if not plan:
        print(f"no plan in {args.album_dir}", file=sys.stderr)
        return 2
    gone = [t for t in plan.tracks if not t.in_source]
    if not gone:
        print("nothing to remove: every track is still in the source")
        return 0
    print("no longer in the source playlist — these files will be deleted:")
    for t in gone:
        print(f"  {t.number:02d} {t.artist} - {t.title}")
    if not args.yes:
        if not sys.stdin.isatty():
            print("add --yes to confirm", file=sys.stderr)
            return 2
        if input("delete them? [y/N] ").strip().lower() not in ("y", "yes", "j", "ja"):
            return 0
    # the library root is what tells prune where the recycle bin is; without it, it would fall
    # back to unlinking, which is exactly the behaviour slice 49 removed
    return exit_code(_service(cfg, _library(args, cfg, required=False)).prune(args.album_dir))


def _systemd(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    from . import systemd

    given = [flag for flag, value in (("--port", args.port), ("--idle-exit", args.idle_exit)) if value is not None]
    if args.action != "install" and given:
        # they describe the units, which only `install` writes — silently doing nothing with them
        # is how you come to believe the service moved to another port
        print(f"{' and '.join(given)} only mean something for 'install': they are written into the units. "
              f"The running service's port is the installed one — 'noaap service status' shows it.", file=sys.stderr)
        return 2
    port, idle_exit = args.port or 8765, args.idle_exit or 900
    try:
        if args.action == "install":
            for line in systemd.install(cfg, port, idle_exit):
                print(line)
            print(f"ready: open http://localhost:{port}/ — the web UI starts on demand and stops after {idle_exit}s idle")
        elif args.action == "uninstall":
            for line in systemd.uninstall():
                print(line)
        elif args.action == "restart":
            for line in systemd.restart(args.force):
                print(line)
        print(systemd.status())
    except (ValueError, RuntimeError) as e:
        print(e, file=sys.stderr)
        return 2
    return 0


def _app(args: argparse.Namespace) -> int:
    from . import desktop, systemd

    url = f"http://127.0.0.1:{args.port or systemd.installed_port()}/"
    try:
        if args.action == "install":
            for line in desktop.install(url, args.browser):
                print(line)
            print("ready: 'noaap' is in the menu — its window is its own, not the browser's")
        elif args.action == "uninstall":
            for line in desktop.uninstall(keep_profile=not args.remove_profile):
                print(line)
        print(desktop.status(url))
    except (OSError, RuntimeError) as e:
        print(e, file=sys.stderr)
        return 2
    return 0


def _delete(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    from .download import load_plan

    plan = load_plan(args.album_dir)
    if not plan:
        print(f"no plan in {args.album_dir}", file=sys.stderr)
        return 2
    if args.track:
        track = next((t for t in plan.tracks if t.video_id == args.track), None)
        if not track:
            print(f"no track {args.track} in this album", file=sys.stderr)
            return 2
        what = f"the track “{track.artist} - {track.title}”"
    else:
        what = f"the album “{plan.albumartist} — {plan.album}” with {len(plan.tracks)} track(s)"
    print(f"about to delete {what} in {args.album_dir}")
    if not args.yes:
        if not sys.stdin.isatty():
            print("add --yes to confirm", file=sys.stderr)
            return 2
        if input("delete? [y/N] ").strip().lower() not in ("y", "yes", "j", "ja"):
            return 0
    library = args.album_dir.resolve().parents[1]
    service = _service(cfg, library)
    outcome = service.delete_track(plan.source_id, args.track) if args.track else service.delete_album(plan.source_id)
    if outcome.message:
        print(outcome.message, file=sys.stderr)
    return exit_code(outcome)


def _serve(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    library = _library(args, cfg, required=True)
    if library is None:
        return 2
    from .web import serve

    serve(cfg, library, host=args.host, port=args.port, idle_exit=args.idle_exit)
    return 0


# -- helpers -------------------------------------------------------------------------------


def _library(args: argparse.Namespace, cfg: config_mod.Config, required: bool) -> Path | None:
    library = getattr(args, "library", None) or cfg.library_root
    if library is None and required:
        print("no library root: run `noaap config --library PATH` or pass --library", file=sys.stderr)
    return library.expanduser() if library else None


def parse_pick(spec: str, count: int) -> list[int]:
    """'1,3-5' -> [0, 2, 3, 4] (0-based, in order, no duplicates). Raises ValueError."""
    if spec.strip().lower() == "all":
        return list(range(count))
    picked: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        for n in range(int(lo), int(hi or lo) + 1):
            if not 1 <= n <= count:
                raise ValueError(f"{n} is not between 1 and {count}")
            if n - 1 not in picked:
                picked.append(n - 1)
    return picked


def _choose(refs: list[SourceRef], args: argparse.Namespace) -> list[SourceRef]:
    spec = "all" if args.all else args.pick
    if spec is None:
        if not sys.stdin.isatty():
            print("\nchoose with --pick 1,3-5 or --all", file=sys.stderr)
            return []
        spec = input("\nwhich ones? (e.g. 1,3-5 / all / empty = none): ")
    try:
        return [refs[i] for i in parse_pick(spec, len(refs))]
    except ValueError as e:
        print(f"invalid choice: {e}", file=sys.stderr)
        return []


def _print_groups(groups: list[tuple[str, list[SourceRef]]], have: set[str]) -> None:
    i = 0
    for label, refs in groups:
        print(f"\n{label}:")
        for r in refs:
            i += 1
            extra = []
            if r.tab == "search" and r.artist:
                extra.append(f"by {r.artist}")
            if r.count:
                extra.append(f"{r.count} tracks")
            print(f"  {i:3d}  {r.title}" + (f"   ({', '.join(extra)})" if extra else "") + ("   ✓ in library" if r.source_id in have else ""))


def _print_track(t: PlanTrack, what: str) -> None:
    status = {"downloaded": "ok  ", "failed": "FAIL"}.get(what, what)
    print(f"  {status} {t.number:02d} {t.artist} - {t.title}" + (f"  ({t.error})" if t.error and what == "failed" else ""))


def _print_plan(plan: AlbumPlan) -> None:
    prov = {k: PROV_MARK.get(v, v) for k, v in plan.provenance.items()}
    print(f"\n{plan.albumartist} — {plan.album}" + (f" ({plan.year})" if plan.year else ""))
    print(f"  kind: {plan.kind}   folder: {plan.folder}")
    print(f"  from: albumartist={prov.get('albumartist', '?')} album={prov.get('album', '?')}" + (f" year={prov['year']}" if "year" in prov else ""))
    for t in plan.tracks:
        marks = f"[{PROV_MARK.get(t.provenance.get('artist'), '?')}/{PROV_MARK.get(t.provenance.get('title'), '?')}]"
        state = "" if t.state == "pending" else f"  <{t.state}>"
        state += "" if t.in_source else "  <no longer in source>"
        print(f"  {t.number:02d}  {t.artist} - {t.title}  {marks}{state}")
    for s in plan.skipped:
        print(f"  --  skipped: {s['title']}  ({s['reason']})")
