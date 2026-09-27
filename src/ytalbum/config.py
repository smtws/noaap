"""Configuration: one TOML file, overridable per run from the CLI."""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

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


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ytalbum" / "config.toml"


@dataclass
class Config:
    library_root: Path | None = None
    # yt-dlp needs a JS runtime for YouTube (DESIGN.md §3.5). None = autodetect.
    js_runtime: str | None = None
    js_runtime_path: str | None = None
    concurrency: int = 2  # parallel YouTube requests; more trips YouTube's bot check sooner
    musicbrainz: bool = True
    lyrics: bool = True  # look lyrics up at lrclib.net and keep them as .lrc + LYRICS tag
    # opt-in, only needed for age-restricted videos (DESIGN.md §7). yt-dlp writes
    # refreshed cookies back into cookies_file.
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None  # "firefox", "chrome", "chrome:Profile 1", …
    # bgutil PO-token generator, needed for some streams (DESIGN.md §3.9).
    # "server": local HTTP server started on demand, stops after pot_idle seconds idle
    # (script mode stays configured as fallback); "script": a Node process per request; "off".
    pot_provider_home: Path | None = None
    pot_mode: str = "server"
    pot_port: int = 4416
    pot_idle: int = 300
    # who may put words on a clock (DESIGN.md §9.36). "none" is the default and the app is exactly
    # what it was without it; "local" needs the `ytalbum[timing]` extra; "http" is another machine
    # running `ytalbum timing-serve`. Nothing is imported until one of the other two is chosen.
    # Two slots, because the two capabilities are bought in different places (§9.40): the machine
    # that aligns best is rarely the one that transcribes best. `timing_provider` is still read and
    # still means both, so nobody's config file breaks.
    timing_provider: str = "none"
    timing_align_provider: str = ""   # empty = whatever `timing_provider` says
    timing_draft_provider: str = ""
    timing_endpoint: str | None = None
    timing_device: str = "auto"  # "auto" | "cpu" | "cuda", for the local provider
    # keys for the providers that are somebody else's computer (§9.37). They are never sent to the
    # page, never logged, and only needed by the provider that is actually configured.
    timing_elevenlabs_key: str = ""
    timing_deepgram_key: str = ""
    # check an alignment against a second method when the `timing-check` extra is installed
    # (§9.38). None = do it whenever it is available; false = never; true = and complain if it is not.
    timing_verify: bool | None = None
    timing_verify_threshold: float = 2.0  # seconds two methods may differ by and still agree
    timing_verify_lost: float = 5.0  # seconds past which one of them has lost the song, not drifted

    def resolved_node(self) -> str | None:
        runtime = self.resolved_js_runtime()
        if runtime and runtime[0] == "node" and runtime[1]:
            return runtime[1]
        return shutil.which("node")

    def resolved_pot_provider(self) -> Path | None:
        """The bgutil `server` dir with a built generate_once.js: configured, or inside the project."""
        candidates = [self.pot_provider_home] if self.pot_provider_home else [PROJECT_POT_HOME]
        return next((c for c in candidates if c and (c / "build" / "generate_once.js").is_file()), None)

    def resolved_js_runtime(self) -> tuple[str, str | None] | None:
        """(name, path) of the runtime to hand to yt-dlp, or None if none is available."""
        if self.js_runtime:
            return self.js_runtime, self.js_runtime_path
        for name in JS_RUNTIMES:
            if path := shutil.which(name):
                return name, path
        return None


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    cfg = Config(
        js_runtime=data.get("js_runtime"),
        js_runtime_path=data.get("js_runtime_path"),
        concurrency=int(data.get("concurrency", 2)),
        musicbrainz=bool(data.get("musicbrainz", True)),
        lyrics=bool(data.get("lyrics", True)),
    )
    if root := data.get("library_root"):
        cfg.library_root = Path(root).expanduser()
    if cookies := data.get("cookies_file"):
        cfg.cookies_file = Path(cookies).expanduser()
    cfg.cookies_from_browser = data.get("cookies_from_browser") or None
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
    return cfg


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


def save_library_root(root: Path, path: Path | None = None) -> Path:
    return save_setting("library_root", str(root), path)
