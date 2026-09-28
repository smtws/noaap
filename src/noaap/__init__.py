"""noaap — turn playlists into properly tagged albums. See DESIGN.md."""


def version() -> str:
    """The installed version, or "0" when run from a checkout that was never installed."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as installed

    try:
        return installed("noaap")
    except PackageNotFoundError:
        return "0"


def user_agent() -> str:
    """What noaap calls itself to LRCLIB and MusicBrainz. Both ask for a contact address, so
    this is the one string that has to be right: it is how they reach whoever is misbehaving."""
    return f"noaap/{version()} ( https://github.com/smtws/noaap )"


def main() -> None:
    import sys

    from .cli import main as cli_main

    sys.exit(cli_main())
