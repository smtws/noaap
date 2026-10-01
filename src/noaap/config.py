"""Configuration: one TOML file, overridable per run from the CLI."""

from __future__ import annotations

import os
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# `uv` installs this project editable, so the repo checkout is two levels above the package
PROJECT_POT_HOME = Path(__file__).resolve().parents[2] / ".pot-provider" / "server"

# yt-dlp's order of preference (see `yt-dlp --help`, --js-runtimes)
JS_RUNTIMES = ("deno", "node", "bun", "quickjs")


# where yt-dlp looks for each browser's profile (Linux), so we only offer what exists
BROWSER_DIRS = {
    "firefox": ("~/.mozilla/firefox", "~/snap/firefox/common/.mozilla/firefox", "~/.var/app/org.mozilla.firefox/.mozilla/firefox"),
    "chrome": ("~/.config/google-chrome",),
    "chromium": ("~/.config/chromium", "~/snap/chromium/common/chromium"),
    "brave": ("~/.config/BraveSoftware/Brave-Browser",),
    "edge": ("~/.config/microsoft-edge",),
    "vivaldi": ("~/.config/vivaldi",),
    "opera": ("~/.config/opera",),
}


def detect_browsers() -> list[str]:
    """Browsers with a profile on this machine, in yt-dlp's naming."""
    return [name for name, dirs in BROWSER_DIRS.items() if any(Path(d).expanduser().is_dir() for d in dirs)]


# What noaap is called on disk, and what it used to be called (§9, slice 52). The old name is
# read, never written: a setting saved here always lands in noaap's own directory.
NAME = "noaap"
LEGACY = "ytalbum"
ENV = "NOAAP_"
LEGACY_ENV = "YTALBUM_"


def config_dir(name: str = NAME) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / name


def cache_dir(name: str = NAME) -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / name


def config_path() -> Path:
    return config_dir() / "config.toml"


def legacy_config_path() -> Path:
    return config_dir(LEGACY) / "config.toml"


def read_path() -> Path:
    """Where settings are read from: ours, or ytalbum's while ours does not exist.

    Without this a user who renames has no `library_root` and every command answers "set the
    library first", which is a worse welcome than a one-line notice.
    """
    if config_path().exists() or not legacy_config_path().exists():
        return config_path()
    return legacy_config_path()


def legacy_notice() -> str | None:
    """One line for whoever is still configured as ytalbum. `noaap migrate` ends it."""
    if read_path() == config_path():
        return None
    return (f"settings read from {legacy_config_path()} (ytalbum's). "
            f"`noaap migrate` copies them to {config_path()}.")


_said: set[str] = set()


def env(name: str) -> str | None:
    """`NOAAP_<name>`, falling back to ytalbum's `YTALBUM_<name>` and saying so once.

    Once per name per process: these are read at import time by several modules, and a line per
    lookup would bury the run in its own notices.
    """
    if (value := os.environ.get(ENV + name)) is not None:
        return value
    if (value := os.environ.get(LEGACY_ENV + name)) is None:
        return None
    if name not in _said:
        _said.add(name)
        print(f"{LEGACY_ENV}{name} is ytalbum's name for {ENV}{name}; it still works.",
              file=sys.stderr)
    return value


class Refused(ValueError):
    """A refusal a person can reach by a wrong setting or a wrong argument.

    **It is one sentence and a non-zero exit, never a traceback.** A stack trace for "nothing is
    watched yet" tells the user nothing they can act on and hides the sentence that would (found by
    the P55 acceptance run). It is its own type rather than a bare `ValueError` so that a genuine
    bug still fails loudly instead of being dressed up as advice.
    """


@dataclass(frozen=True)
class Watch:
    """One folder the watcher looks at, and what it means.

    `intake`: what is dropped there is copied into the library and the folder is left as it is.
    `library`: an adopted library watching itself, where the owner adds and changes files in place.
    """

    name: str
    folder: Path
    shape: str = "intake"   # intake | library

    def holds(self, other: Path) -> bool:
        return other == self.folder or self.folder in other.parents


@dataclass
class Config:
    library_root: Path | None = None
    # yt-dlp needs a JS runtime for YouTube (DESIGN.md §3.5). None = autodetect.
    js_runtime: str | None = None
    js_runtime_path: str | None = None
    concurrency: int = 2  # parallel YouTube requests; more trips YouTube's bot check sooner
    musicbrainz: bool = True
    lyrics: bool = True  # look lyrics up at lrclib.net and keep them as .lrc + LYRICS tag
    # **the state the library is in** (§9, slice 100): what every album should have. A later pass
    # brings the albums it touches to these; an album can only be *excepted* from one, in its own
    # view, and nothing but the user ever writes an exception.
    cover_beside: bool = True        # cover.jpg in the album folder
    cover_embedded: bool = True      # and the picture inside each file
    lyrics_embedded: bool = True     # the words in the file's tag, beside the .lrc
    rename_adopted: bool = False     # rename an adopted album's files into noaap's scheme
    retag_adopted: bool = False      # and write noaap's tags into them
    # **whether a pass may clear empty folders that are not of its own making** (§9, slice 104).
    # The user: *"maybe we should make clear empty folders a setting?"*. Off, a pass removes only
    # the folders it emptied itself — which is what stopped it deleting `Der W/Autonomie`, empty
    # in their own collection and never touched by us. On, it also takes away empty folders it
    # finds under the root it works on, theirs included, and the dry run names every one first.
    remove_empty_folders: bool = False
    # opt-in, only needed for age-restricted videos (DESIGN.md §7). yt-dlp writes
    # refreshed cookies back into cookies_file.
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None  # "firefox", "chrome", "chrome:Profile 1", …
    # SoundCloud's own, and **only** SoundCloud's (§9, slice 57). Free tracks need no account at
    # all, so these default to none; one site's credentials are never handed to another's.
    soundcloud_cookies_file: Path | None = None
    soundcloud_cookies_from_browser: str | None = None
    # Patreon's own, and **only** Patreon's: a patron's session is theirs and no provider inherits
    # another's cookies (§9, slice 70). Both default to nothing, and with nothing the provider refuses
    # rather than trying anonymously — measured: even a public post answers 403 without a session.
    patreon_cookies_file: Path | None = None
    patreon_cookies_from_browser: str | None = None
    # how many of a campaign's posts a listing reads before it stops and says so. A creator with two
    # thousand posts is not a library and nobody asked for a backup of them (§9, slice 70).
    patreon_post_cap: int = 200
    # **off, and it stays off until somebody says otherwise** (§9, slice 72). A creator who posts a
    # narrated story as video has audio in it and nothing else; with this on, that audio is copied out
    # — never re-encoded, never the picture, and never for a post with any protection on it. Off, such
    # a post is refused in a sentence that names this setting.
    patreon_audio_from_video: bool = False
    # …and the words that came with such a post, if it has captions (§9, slice 74). Off by default and
    # only ever a sidecar: they are the creator's text, never written into the audio file's tag and
    # never offered to anybody.
    patreon_captions: bool = False
    # bgutil PO-token generator, needed for some streams (DESIGN.md §3.9).
    # "server": local HTTP server started on demand, stops after pot_idle seconds idle
    # (script mode stays configured as fallback); "script": a Node process per request; "off".
    pot_provider_home: Path | None = None
    pot_mode: str = "server"
    pot_port: int = 4416
    pot_idle: int = 300
    # who may put words on a clock (DESIGN.md §9, slice 36). "none" is the default and the app is exactly
    # what it was without it; "local" needs the `noaap[timing]` extra; "http" is another machine
    # running `noaap timing-serve`. Nothing is imported until one of the other two is chosen.
    # Two slots, because the two capabilities are bought in different places (§9, slice 40): the machine
    # that aligns best is rarely the one that transcribes best. `timing_provider` is still read and
    # still means both, so nobody's config file breaks.
    timing_provider: str = "none"
    timing_align_provider: str = ""   # empty = whatever `timing_provider` says
    timing_draft_provider: str = ""
    timing_endpoint: str | None = None
    timing_device: str = "auto"  # "auto" | "cpu" | "cuda", for the local provider
    # keys for the providers that are somebody else's computer (§9, slice 37). They are never sent to the
    # page, never logged, and only needed by the provider that is actually configured.
    timing_elevenlabs_key: str = ""
    timing_deepgram_key: str = ""
    # check an alignment against a second method when the `timing-check` extra is installed
    # (§9, slice 38). None = do it whenever it is available; false = never; true = and complain if it is not.
    timing_verify: bool | None = None
    timing_verify_threshold: float = 2.0  # seconds two methods may differ by and still agree
    timing_verify_lost: float = 5.0  # seconds past which one of them has lost the song, not drifted
    # how long `noaap timing-serve` keeps its models loaded with nothing to do (§9, slice 41). 0 = for
    # ever, which is what a machine dedicated to this wants.
    timing_idle_minutes: float = 5.0
    # the same idea for the app's own server, in seconds, because a laptop shares its card with the
    # desktop in front of it (§9, slice 82). The default comes from measurements on this one: one
    # alignment holds **3608 MiB** of an 8 GB card while the provider lives, and loading every model
    # again off a warm disk costs about **two seconds**. A minute covers working track by track —
    # align, read the placement, fix a line, align the next — and where the window does expire in the
    # middle of that, it costs those two seconds once. 0 = hold them for ever.
    timing_card_idle_seconds: float = 60.0
    # folders `noaap watch` looks at (§9, slice 59). Empty is the default and nothing is watched;
    # ruling 4 of R-200 is why it is a separate unit and not a thread in the web service.
    watches: list[Watch] = field(default_factory=list)

    def watch(self, name: str) -> Watch | None:
        """The configured root of that name, or None. **The only way a path enters the watcher**:
        the API takes a name and a relative path, never a path of its own (§9, slice 59)."""
        return next((w for w in self.watches if w.name == name), None)

    def resolved_node(self) -> str | None:
        runtime = self.resolved_js_runtime()
        if runtime and runtime[0] == "node" and runtime[1]:
            return runtime[1]
        return shutil.which("node")

    def resolved_pot_provider(self) -> Path | None:
        """The bgutil `server` dir with a built generate_once.js: configured, or inside the project."""
        candidates = [self.pot_provider_home] if self.pot_provider_home else [PROJECT_POT_HOME]
        return next((c for c in candidates if c and (c / "build" / "generate_once.js").is_file()), None)

    def resolved_ffmpeg(self) -> str | None:
        """Where ffmpeg is, or None.

        Nothing in noaap can be configured to point elsewhere: `trim.py` runs the bare name and
        yt-dlp looks it up the same way, so `PATH` is the whole answer. It is reported rather than
        enforced, because everything except downloading and trimming works without it.
        """
        return shutil.which("ffmpeg")

    def resolved_js_runtime(self) -> tuple[str, str | None] | None:
        """(name, path) of the runtime to hand to yt-dlp, or None if none is available."""
        if self.js_runtime:
            return self.js_runtime, self.js_runtime_path
        for name in JS_RUNTIMES:
            if path := shutil.which(name):
                return name, path
        return None


def load(path: Path | None = None) -> Config:
    path = path or read_path()
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    cfg = Config(
        js_runtime=data.get("js_runtime"),
        js_runtime_path=data.get("js_runtime_path"),
        concurrency=int(data.get("concurrency", 2)),
        musicbrainz=bool(data.get("musicbrainz", True)),
        lyrics=bool(data.get("lyrics", True)),
        # the state the library is in: what every album should have (§9, slice 100)
        cover_beside=bool(data.get("cover_beside", True)),
        cover_embedded=bool(data.get("cover_embedded", True)),
        lyrics_embedded=bool(data.get("lyrics_embedded", True)),
        rename_adopted=bool(data.get("rename_adopted", False)),
        retag_adopted=bool(data.get("retag_adopted", False)),
        remove_empty_folders=bool(data.get("remove_empty_folders", False)),
    )
    if root := data.get("library_root"):
        cfg.library_root = Path(root).expanduser()
    if cookies := data.get("cookies_file"):
        cfg.cookies_file = Path(cookies).expanduser()
    cfg.cookies_from_browser = data.get("cookies_from_browser") or None
    if cookies := data.get("soundcloud_cookies_file"):
        cfg.soundcloud_cookies_file = Path(cookies).expanduser()
    if cookies := data.get("patreon_cookies_file"):
        cfg.patreon_cookies_file = Path(cookies).expanduser()
    cfg.soundcloud_cookies_from_browser = data.get("soundcloud_cookies_from_browser") or None
    cfg.patreon_cookies_from_browser = data.get("patreon_cookies_from_browser") or None
    if cap := data.get("patreon_post_cap"):
        cfg.patreon_post_cap = int(cap)
    cfg.patreon_audio_from_video = bool(data.get("patreon_audio_from_video", False))
    cfg.patreon_captions = bool(data.get("patreon_captions", False))
    if pot := data.get("pot_provider_home"):
        cfg.pot_provider_home = Path(pot).expanduser()
    cfg.pot_mode = str(data.get("pot_mode", "server"))
    cfg.pot_port = int(data.get("pot_port", 4416))
    cfg.pot_idle = int(data.get("pot_idle", 300))
    # flat keys like the rest of this file, and a `[timing]` table for whoever prefers to group them
    timing = data.get("timing") if isinstance(data.get("timing"), dict) else {}
    cfg.timing_provider = str(data.get("timing_provider") or timing.get("provider") or "none")
    cfg.timing_align_provider = str(data.get("timing_align_provider") or timing.get("align_provider") or "")
    cfg.timing_draft_provider = str(data.get("timing_draft_provider") or timing.get("draft_provider") or "")
    cfg.timing_endpoint = (data.get("timing_endpoint") or timing.get("endpoint") or None) or None
    cfg.timing_device = str(data.get("timing_device") or timing.get("device") or "auto")
    cfg.timing_elevenlabs_key = str(data.get("timing_elevenlabs_key") or timing.get("elevenlabs_key") or "")
    cfg.timing_deepgram_key = str(data.get("timing_deepgram_key") or timing.get("deepgram_key") or "")
    verify = data.get("timing_verify", timing.get("verify"))
    cfg.timing_verify = None if verify is None else bool(verify)
    cfg.timing_verify_threshold = float(data.get("timing_verify_threshold")
                                        or timing.get("verify_threshold") or 2.0)
    cfg.timing_verify_lost = float(data.get("timing_verify_lost")
                                   or timing.get("verify_lost") or 5.0)
    idle = data.get("timing_idle_minutes", timing.get("idle_minutes"))
    cfg.timing_idle_minutes = 5.0 if idle is None else float(idle)
    card = data.get("timing_card_idle_seconds", timing.get("card_idle_seconds"))
    cfg.timing_card_idle_seconds = 60.0 if card is None else float(card)
    cfg.watches = _watches(data)
    return cfg


def _watches(data: dict[str, Any]) -> list[Watch]:
    """`[[watch]]` tables, in the order they are written."""
    out = []
    for row in data.get("watch") or []:
        if not isinstance(row, dict) or not row.get("folder"):
            continue
        folder = Path(str(row["folder"])).expanduser()
        shape = str(row.get("shape") or "intake")
        out.append(Watch(name=str(row.get("name") or folder.name), folder=folder,
                         shape=shape if shape in ("intake", "library") else "intake"))
    return out


def watch_trouble(watches: list[Watch], library: Path | None) -> list[str]:
    """Why a set of watched folders cannot stand, in sentences a person can act on.

    **The two shapes may not be nested** (R-200, ruling 1). An intake folder inside a library would
    have every drop taken twice — once as an arrival and once as the library's own album — and a
    library inside an intake folder would have the intake pass copy the library into itself. A
    library watching itself is the `library` shape and needs no intake at all.
    """
    out = []
    seen: dict[str, Watch] = {}
    for watch in watches:
        if not watch.folder.is_absolute():
            out.append(f"{watch.name}: {watch.folder} is not an absolute path")
        if watch.name in seen:
            out.append(f"{watch.name}: two watched folders share that name")
        seen[watch.name] = watch
        if library and watch.shape == "intake" and (watch.holds(library) or library == watch.folder
                                                    or _inside(watch.folder, library)):
            out.append(f"{watch.name}: an intake folder may not hold the library, and the library "
                       "may not hold it — a library that watches itself is the `library` shape")
    for a in watches:
        for b in watches:
            if a is not b and a.holds(b.folder):
                out.append(f"{b.name}: lies inside {a.name}; watched folders may not be nested")
    return out


def _inside(folder: Path, other: Path) -> bool:
    return folder == other or other in folder.parents


def save_setting(name: str, value: str | bool | int | None, path: Path | None = None) -> Path:
    """Set (or with None: remove) one top-level setting, keeping all other lines."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [line for line in lines if line.split("=")[0].strip() != name]
    if isinstance(value, bool):
        lines.insert(0, f"{name} = {'true' if value else 'false'}")
    elif isinstance(value, int):
        lines.insert(0, f"{name} = {value}")
    elif value is not None:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.insert(0, f'{name} = "{escaped}"')
    path.write_text("\n".join(lines) + "\n")
    return path


def save_watches(watches: list[Watch], path: Path | None = None) -> Path:
    """Write the `[[watch]]` tables, keeping every other line of the file (§9, slice 92).

    The settings view can edit these now, and this is the one writer: the tables are replaced whole,
    because they are a list and a half-replaced list is not one. Everything that is not a `[[watch]]`
    table — including comments the user put there — is kept in its order.
    """
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    kept: list[str] = []
    inside = False
    for line in path.read_text().splitlines() if path.exists() else []:
        stripped = line.strip()
        if stripped.startswith("[["):
            inside = stripped.replace(" ", "") == "[[watch]]"
        elif stripped.startswith("[") and stripped.endswith("]"):
            inside = False
        if not inside:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    for watch in watches:
        folder = str(watch.folder).replace("\\", "\\\\").replace('"', '\\"')
        name = str(watch.name).replace("\\", "\\\\").replace('"', '\\"')
        kept += ["", "[[watch]]", f'name = "{name}"', f'folder = "{folder}"',
                 f'shape = "{watch.shape if watch.shape in ("intake", "library") else "intake"}"']
    path.write_text("\n".join(kept).lstrip("\n") + "\n")
    return path


def save_library_root(root: Path, path: Path | None = None) -> Path:
    return save_setting("library_root", str(root), path)
