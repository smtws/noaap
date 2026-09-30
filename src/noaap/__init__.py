"""noaap — turn playlists into properly tagged albums. See DESIGN.md."""


def version() -> str:
    """The installed version, or "0" when run from a checkout that was never installed."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as installed

    try:
        return installed("noaap")
    except PackageNotFoundError:
        return "0"


def running_from() -> tuple[str, str]:
    """(what version this process is, where its code comes from) — for the page and the CLI.

    **Which code is running is a question the page has to be able to answer** (§9, slice 93). The
    service used to run an editable install pointing at the working tree, so "1.22.0" was a claim about
    a directory somebody might be editing. A release install is a snapshot and says only its version; a
    checkout says so, with `git describe` while git can be asked, so the two can never be confused.
    """
    from pathlib import Path

    here = Path(__file__).resolve().parent
    # an editable install leaves the package in the checkout's `src/`; a real install is in site-packages
    checkout = here.parent.parent if (here.parent.parent / ".git").is_dir() and here.parent.name == "src" else None
    if checkout is None:
        return version(), "release install"
    return version(), f"checkout {_described(checkout)}"


_DESCRIBED = {}   # checkout -> `git describe`; this module keeps its imports out of module scope


def _described(checkout) -> str:
    """`git describe` of that checkout, asked once per process: it cannot change under us."""
    import subprocess

    if checkout in _DESCRIBED:
        return _DESCRIBED[checkout]
    try:
        r = subprocess.run(["git", "-C", str(checkout), "describe", "--tags", "--always", "--dirty"],
                           capture_output=True, text=True, timeout=5)
        said = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        said = ""
    _DESCRIBED[checkout] = said or str(checkout)
    return _DESCRIBED[checkout]


def user_agent() -> str:
    """What noaap calls itself to LRCLIB and MusicBrainz. Both ask for a contact address, so
    this is the one string that has to be right: it is how they reach whoever is misbehaving."""
    return f"noaap/{version()} ( https://github.com/smtws/noaap )"


def main() -> None:
    import sys

    from .cli import main as cli_main

    sys.exit(cli_main())
