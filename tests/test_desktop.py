"""The desktop launcher: its own window class, so the taskbar does not call it a browser."""

import pytest

from noaap import desktop


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


def test_entry_asks_for_a_window_of_its_own():
    entry = desktop.render_entry("/usr/bin/google-chrome", "http://127.0.0.1:8765/")
    # the taskbar groups by the window class, so the launcher must claim it and set it
    assert "StartupWMClass=noaap" in entry
    assert "--class=noaap" in entry
    # ...which a browser only honours in a process of its own
    assert "--user-data-dir=" in entry
    assert "--app=http://127.0.0.1:8765/" in entry
    assert "Icon=noaap" in entry


def test_install_writes_launcher_and_icons(data_home):
    done = desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    entry = data_home / "applications" / "noaap.desktop"
    assert entry.exists() and "Exec=/usr/bin/google-chrome" in entry.read_text()
    assert any((data_home / "icons" / "hicolor").glob("*/apps/noaap.*"))
    assert desktop.profile_dir().is_dir()
    assert any("noaap.desktop" in line for line in done)


def test_install_without_a_browser_says_so(data_home, monkeypatch):
    monkeypatch.setattr(desktop, "find_browser", lambda: None)
    with pytest.raises(RuntimeError, match="no Chromium-based browser"):
        desktop.install("http://127.0.0.1:8765/")


def test_uninstall_keeps_the_profile_unless_asked(data_home):
    desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    desktop.uninstall()
    assert not desktop.desktop_file().exists()
    assert not any((data_home / "icons" / "hicolor").glob("*/apps/noaap.*"))
    assert desktop.profile_dir().is_dir()  # logins and window size live here

    desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    desktop.uninstall(keep_profile=False)
    assert not desktop.profile_dir().exists()


def test_status_reports_both_states(data_home):
    assert "not installed" in desktop.status("http://127.0.0.1:8765/")
    desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    assert "window class: noaap" in desktop.status("http://127.0.0.1:8765/")


# -- and what ytalbum's launcher left behind (§9, slice 52) --------------------------------


def ytalbums_launcher(data_home):
    (data_home / "applications").mkdir(parents=True, exist_ok=True)
    (data_home / "applications" / "ytalbum.desktop").write_text("[Desktop Entry]\nName=ytalbum\n")
    icon = data_home / "icons" / "hicolor" / "48x48" / "apps" / "ytalbum.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(b"\x89PNG")
    (data_home / "ytalbum-browser" / "Default").mkdir(parents=True, exist_ok=True)


def test_our_launcher_is_its_own_and_ytalbums_is_only_reported(data_home):
    ytalbums_launcher(data_home)
    desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")

    assert desktop.profile_dir() != data_home / "ytalbum-browser", "our own cookies, our own window"
    assert desktop.profile_dir().is_dir()
    assert "ytalbum's launcher is still here (3 files)" in desktop.status("http://127.0.0.1:8765/")

    desktop.uninstall()

    assert (data_home / "applications" / "ytalbum.desktop").exists()
    assert (data_home / "icons" / "hicolor" / "48x48" / "apps" / "ytalbum.png").exists()
    assert (data_home / "ytalbum-browser").is_dir(), "never: it holds whatever they are signed into"


def test_on_a_machine_without_the_old_launcher_nothing_is_said(data_home):
    desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    assert desktop.legacy_installed() == []
    # the phrase, not the word: `tmp_path` is named after the test, so a test with the old name in
    # it would find that name in its own temporary directory (this has bitten twice now)
    assert "launcher is still here" not in desktop.status("http://127.0.0.1:8765/")
