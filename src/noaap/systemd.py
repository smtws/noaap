"""`noaap service install|uninstall|status`: the web UI as a socket-activated systemd user service.

systemd listens on the port; the first request starts `noaap serve --idle-exit …`, which
stops itself when idle and is started again by the next request. User level only (no root).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

from .config import Config, Refused, watch_trouble

UNIT = "noaap"
LEGACY_UNIT = "ytalbum"          # ytalbum's units are left alone; `noaap migrate` offers to remove them
DEFAULT_IDLE_EXIT = 900

# **The service runs a released snapshot, not the working tree** (§9, slice 93). An editable install
# points at a checkout, and this service restarts itself: `--idle-exit` plus socket activation means the
# next request after an idle window starts a *new* process from whatever the tree holds at that second —
# half-edited, possibly not importable. The user saw the milder half of it on 2026-09-30: the page files
# are read per request, so a page from a newer commit met an older process and the settings view was
# mixed. A unit that names a venv holding the released wheel cannot do either.
# `NOAAP_RELEASE_VENV` is honoured so that this and `scripts/release-install.sh` cannot disagree about
# where the release lives; the script warns when the installed unit names a different one.
RELEASE_VENV = Path(os.environ.get("NOAAP_RELEASE_VENV") or Path.home() / ".local" / "noaap-release")


def release_exe(venv: Path | None = None) -> Path | None:
    """The release venv's `noaap`, if that venv is there. None means "there is no release install"."""
    exe = (venv or RELEASE_VENV) / "bin" / "noaap"
    return exe if exe.is_file() else None


def unit_exe(venv: Path | None = None, from_checkout: bool = False) -> Path:
    """Which `noaap` a unit should start: the release venv's when it exists, else this one's.

    `from_checkout` is the deliberate way back for development — a unit that follows the tree again,
    which is what my own verification wants and what the user's service must not be.
    """
    if not from_checkout and (exe := release_exe(venv)):
        return exe
    return Path(sys.prefix) / "bin" / "noaap"


def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "systemd" / "user"


def render_units(cfg: Config, port: int = 8765, idle_exit: int = DEFAULT_IDLE_EXIT,
                 from_checkout: bool = False) -> dict[str, str]:
    exe = unit_exe(from_checkout=from_checkout)
    path = ["/usr/local/bin", "/usr/bin", "/bin"]
    if node := cfg.resolved_node():  # systemd does not see nvm's PATH; yt-dlp and the token server need node
        path.insert(0, str(Path(node).parent))
    return {
        f"{UNIT}.socket": f"""[Unit]
Description=noaap web UI (listens, starts the service on demand)

[Socket]
ListenStream=127.0.0.1:{port}

[Install]
WantedBy=sockets.target
""",
        f"{UNIT}.service": f"""[Unit]
Description=noaap web UI (started by {UNIT}.socket, stops itself when idle)
Requires={UNIT}.socket
After={UNIT}.socket

[Service]
ExecStart={exe} serve --idle-exit {idle_exit}
Environment=PATH={":".join(path)}
Environment=PYTHONUNBUFFERED=1
""",
    }


WATCH_UNIT = f"{UNIT}-watch"


def render_watch_unit(cfg: Config, port: int = 8765, from_checkout: bool = False) -> dict[str, str]:
    """The watcher's own unit. **Not installed with the web service** (R-200, ruling 4).

    It is always on, where the web service is started on demand and stops itself again — so it is a
    thing a person turns on deliberately, and turns off by stopping this one unit. `Restart=always`
    because a watcher that has quietly died looks exactly like a folder where nothing arrives.

    It runs the release venv's `noaap` on the same reasoning as the web service (§9, slice 93): a
    watcher that restarts every 30 s would otherwise pick the working tree up faster than anything.
    """
    exe = unit_exe(from_checkout=from_checkout)
    path = ["/usr/local/bin", "/usr/bin", "/bin"]
    if node := cfg.resolved_node():
        path.insert(0, str(Path(node).parent))
    return {
        f"{WATCH_UNIT}.service": f"""[Unit]
Description=noaap watcher (hands what arrives in a watched folder to the web UI)
After=network.target

[Service]
ExecStart={exe} watch --port {port}
Restart=always
RestartSec=30
Environment=PATH={":".join(path)}
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
""",
    }


def install_watch(cfg: Config, port: int | None = None, from_checkout: bool = False) -> list[str]:
    """Write and start the watcher's unit. Refuses a configuration that cannot stand."""
    if not cfg.watches:
        raise Refused("nothing is watched yet — put a [[watch]] table in the config file "
                      "(`noaap watch --help`), then install the service")
    if trouble := watch_trouble(cfg.watches, cfg.library_root):
        raise Refused("; ".join(trouble))
    done = []
    unit_dir().mkdir(parents=True, exist_ok=True)
    for name, text in render_watch_unit(cfg, port or installed_port(), from_checkout).items():
        (unit_dir() / name).write_text(text)
        done.append(f"wrote {unit_dir() / name}")
    for args in (("daemon-reload",), ("enable", "--now", f"{WATCH_UNIT}.service")):
        r = systemctl(*args)
        if r.returncode:
            raise Refused(f"systemctl --user {' '.join(args)}: {r.stderr.strip()}")
        done.append(f"systemctl --user {' '.join(args)}")
    return done


def uninstall_watch() -> list[str]:
    done = []
    systemctl("disable", "--now", f"{WATCH_UNIT}.service")
    done.append(f"systemctl --user disable --now {WATCH_UNIT}.service")
    path = unit_dir() / f"{WATCH_UNIT}.service"
    if path.exists():
        path.unlink()
        done.append(f"removed {path}")
    systemctl("daemon-reload")
    return done


def watching() -> str:
    """One line for `service status`: the watcher is a separate unit and says so separately."""
    state = systemctl("is-active", f"{WATCH_UNIT}.service").stdout.strip()
    return state or "not installed"


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def legacy_units() -> list[Path]:
    """ytalbum's unit files, if this machine still has them. Nothing here removes them."""
    return [p for name in (f"{LEGACY_UNIT}.socket", f"{LEGACY_UNIT}.service") if (p := unit_dir() / name).exists()]


def port_of(path: Path, default: int | None = None) -> int | None:
    match = re.search(r"ListenStream=.*?:(\d+)", path.read_text()) if path.exists() else None
    return int(match[1]) if match else default


def clash(port: int) -> str | None:
    """ytalbum holding the same port, which is the one way `install` fails for a reason of ours.

    Both default to 8765. systemd would answer "Address already in use" from inside an
    `enable --now`, which reads as a bug in noaap rather than as two versions of the same program
    wanting one port.
    """
    theirs = unit_dir() / f"{LEGACY_UNIT}.socket"
    if not theirs.exists() or port_of(theirs) != port:
        return None
    if systemctl("is-active", f"{LEGACY_UNIT}.socket").stdout.strip() != "active":
        return None
    return (f"ytalbum is already listening on port {port} ({theirs}). Either give noaap another port "
            f"(`noaap service install --port 8766`) or stop ytalbum's first "
            f"(`systemctl --user disable --now {LEGACY_UNIT}.socket`, or `noaap migrate --apply "
            f"--uninstall-old`). Nothing was changed.")


def install(cfg: Config, port: int = 8765, idle_exit: int = DEFAULT_IDLE_EXIT,
            from_checkout: bool = False) -> list[str]:
    """Write the units, enable and start the socket. Returns what was done, for the user.

    Rewriting an installed unit is how a unit is *moved* to the release venv: the same command, once
    the venv is there (§9, slice 93).
    """
    if not cfg.library_root:
        raise Refused("set the library first: noaap config --library PATH")
    if message := clash(port):
        raise Refused(message)
    done = []
    unit_dir().mkdir(parents=True, exist_ok=True)
    for name, text in render_units(cfg, port, idle_exit, from_checkout).items():
        (unit_dir() / name).write_text(text)
        done.append(f"wrote {unit_dir() / name}")
    done.append(f"the service will run {unit_exe(from_checkout=from_checkout)}"
                + ("" if release_exe() and not from_checkout
                   else f" — no release install at {RELEASE_VENV}, so it follows this checkout"))
    for args in (("daemon-reload",), ("enable", "--now", f"{UNIT}.socket")):
        r = systemctl(*args)
        if r.returncode:
            raise Refused(f"systemctl --user {' '.join(args)}: {r.stderr.strip()}")
        done.append(f"systemctl --user {' '.join(args)}")
    return done


def uninstall() -> list[str]:
    """Remove the web service — and the watcher with it, since it exists to talk to it."""
    done = uninstall_watch() if (unit_dir() / f"{WATCH_UNIT}.service").exists() else []
    for args in (("disable", "--now", f"{UNIT}.socket"), ("stop", f"{UNIT}.service")):
        systemctl(*args)
        done.append(f"systemctl --user {' '.join(args)}")
    for name in (f"{UNIT}.socket", f"{UNIT}.service"):
        path = unit_dir() / name
        if path.exists():
            path.unlink()
            done.append(f"removed {path}")
    systemctl("daemon-reload")
    return done


def installed_port(default: int = 8765) -> int:
    """Our own socket's port. Deliberately not ytalbum's: restarting or talking to their service
    from here would be acting on a program this one does not manage."""
    return port_of(unit_dir() / f"{UNIT}.socket", default) or default


def busy(port: int | None = None) -> bool:
    """True if the running web UI has a queued or running job (nothing to interrupt if not)."""
    try:
        r = httpx.get(f"http://127.0.0.1:{port or installed_port()}/api/state", timeout=2)
        state = r.json()
        return bool(state.get("busy_write", state.get("busy")))  # a running search is cheap to lose
    except (httpx.HTTPError, ValueError):
        return False


def restart(force: bool = False) -> list[str]:
    """Restart the service, but never while it is working (that would kill the job)."""
    if not force and busy():
        raise Refused("a job is running — wait for it, cancel it in the web UI, or use --force")
    r = systemctl("restart", f"{UNIT}.service")
    if r.returncode:
        raise Refused(f"systemctl --user restart: {r.stderr.strip()}")
    return [f"systemctl --user restart {UNIT}.service"]


def status() -> str:
    socket = systemctl("is-active", f"{UNIT}.socket").stdout.strip() or "not installed"
    service = systemctl("is-active", f"{UNIT}.service").stdout.strip()
    line = f"socket: {socket}   web UI process: {'running' if service == 'active' else 'stopped (starts on the next request)'}"
    if theirs := legacy_units():
        line += f"\n  ytalbum's units are still here ({len(theirs)}): `noaap migrate` can remove them"
    return line
