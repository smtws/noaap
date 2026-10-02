"""`noaap migrate`: take over what ytalbum left on this machine (DESIGN §9, slice 52).

Nothing here is needed to *run* — the settings and the `YTALBUM_*` variables are read where they
are, and the library was never touched by the rename. This exists so that the old names stop being
read, and so the two caches do not have to refill.

It **copies, never moves**, and it leaves ytalbum's own directories alone. That is what makes the
undo trivial: ytalbum still works afterwards, because nothing of its was taken away.

Dry by default, which is the opposite of `--dry-run` elsewhere in this program. The reason is that
this is the one command that reaches outside the library and into the user's configuration and their
systemd units, where "show me first" is the only reasonable default.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import config, desktop, download, systemd

# runtime scratch, not worth carrying: a heartbeat file and the token server's log
CACHES = ("lyrics.sqlite3", "musicbrainz.sqlite3")


@dataclass
class Copy:
    src: Path
    dst: Path
    skip: str | None = None  # why not, if not


def copies() -> list[Copy]:
    """Everything worth carrying over, with the reason where one is skipped.

    The settings file, whatever `*.env` the user keeps beside it (a vendor key lives in one here —
    its name is read, never its contents), and the two sqlite caches with their write-ahead files.
    """
    out: list[Copy] = []
    theirs, ours = config.config_dir(config.LEGACY), config.config_dir()
    for src in sorted([theirs / "config.toml", *theirs.glob("*.env")]):
        if src.exists():
            out.append(Copy(src, ours / src.name))
    old_cache, new_cache = config.cache_dir(config.LEGACY), config.cache_dir()
    for name in CACHES:
        for src in sorted(old_cache.glob(f"{name}*")):  # the db, plus -wal/-shm if it was left open
            out.append(Copy(src, new_cache / src.name))
    for copy in out:
        if copy.dst.exists():
            copy.skip = "already here"
    return out


def legacy_profile() -> Path:
    return desktop.data_home() / f"{desktop.LEGACY_APP_ID}-browser"


def removals() -> list[Path]:
    """ytalbum's units, its launcher entry and its icons — and never its browser profile.

    Its own config and cache directories are not here either: they are what makes going back to
    ytalbum possible, and this command is not the place to close that door.
    """
    profile = legacy_profile()
    return systemd.legacy_units() + [p for p in desktop.legacy_installed() if p != profile]


def old_plans(roots: Iterable[Path]) -> list[tuple[Path, str]]:
    """Every `.ytalbum.json` under these roots, with why it cannot be renamed where that is so.

    The plan file is `.noaap.json` since 1.30.0 (R-419) and nothing reads the old name any more, so
    an album that still holds one is an album noaap cannot see. This finds them; `run` renames them.
    """
    out: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for root in roots:
        if root is None or not Path(root).is_dir():
            continue
        for path in sorted(Path(root).rglob(download.OLD_PLAN_FILE)):
            if path in seen:
                continue
            seen.add(path)
            beside = path.with_name(download.PLAN_FILE)
            out.append((path, f"{beside.name} is already there" if beside.exists() else ""))
    return out


def rename_plans(roots: Iterable[Path], apply: bool = False) -> list[str]:
    """What the sidecars would be called, or what they are called now. One line each, plus a count."""
    found = old_plans(roots)
    lines: list[str] = []
    done = blocked = 0
    for path, why in found:
        if why:
            lines.append(f"left alone: {path} — {why}, so both names are there and neither is this "
                         "pass's to choose")
            blocked += 1
        elif apply:
            path.rename(path.with_name(download.PLAN_FILE))     # atomic within the folder
            lines.append(f"renamed {path} → {download.PLAN_FILE}")
            done += 1
        else:
            lines.append(f"would rename {path} → {download.PLAN_FILE}")
            done += 1
    if found:
        lines.append(f"{done} plan file(s) {'renamed' if apply else 'to rename'}"
                     + (f", {blocked} left alone" if blocked else ""))
    return lines


def run(apply: bool = False, uninstall_old: bool = False, library: Path | None = None,
        watched: Iterable[Path] = ()) -> list[str]:
    """What was done, or what would be. One line each, for the user to read before saying yes.

    The watcher is not part of this in either direction: ytalbum never had one, so there is nothing
    of theirs to stop, and noaap's own is installed by hand and removed with the web service
    (§9, slice 59).
    """
    lines: list[str] = []
    # **the plan files first**, because every other pass of this program depends on the name
    lines += rename_plans([p for p in (library, *watched) if p], apply=apply)
    for copy in copies():
        if copy.skip:
            lines.append(f"skipped {copy.dst} — {copy.skip}")
        elif apply:
            copy.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(copy.src, copy.dst)
            lines.append(f"copied {copy.src} → {copy.dst}")
        else:
            lines.append(f"would copy {copy.src} → {copy.dst}")

    if uninstall_old:
        for unit in (f"{systemd.LEGACY_UNIT}.socket", f"{systemd.LEGACY_UNIT}.service"):
            if not (systemd.unit_dir() / unit).exists():
                continue
            if apply:
                if unit.endswith(".socket"):
                    systemd.systemctl("disable", "--now", unit)
                else:
                    systemd.systemctl("stop", unit)
                lines.append(f"stopped {unit}")
            else:
                lines.append(f"would stop and disable {unit}")
        for path in removals():
            if apply:
                path.unlink()
                lines.append(f"removed {path}")
            else:
                lines.append(f"would remove {path}")
        if apply and systemd.legacy_units() == []:
            systemd.systemctl("daemon-reload")
    elif removals():
        lines.append(f"ytalbum's units and launcher are still installed ({len(removals())} files) — "
                     "add --uninstall-old to remove them")

    if (profile := legacy_profile()).is_dir():
        lines.append(f"left alone: {profile} (ytalbum's browser profile — it holds your cookies "
                     "and logins; delete it yourself if you want it gone)")
    lines.append(f"left alone: {config.config_dir(config.LEGACY)} and {config.cache_dir(config.LEGACY)} — "
                 "nothing is removed from them, so ytalbum still runs")
    if not apply:
        lines.append("nothing was changed. `noaap migrate --apply` does it.")
    elif uninstall_old:
        lines.append("to go back: `ytalbum service install` and `ytalbum app install` rebuild what "
                     "was removed.")
    return lines
