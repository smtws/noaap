"""Socket activation: unit files, and serving on a socket handed over like systemd does."""

import socket
import threading
from pathlib import Path

import httpx
import pytest

from noaap.config import Config, Refused
from noaap.systemd import install, render_units
from noaap.web import App


def test_units_start_the_service_on_demand_with_node_on_the_path():
    units = render_units(Config(js_runtime="node", js_runtime_path="/opt/node/bin/node"), port=9000, idle_exit=600)
    assert "ListenStream=127.0.0.1:9000" in units["noaap.socket"]
    service = units["noaap.service"]
    assert "serve --idle-exit 600" in service
    assert "Environment=PATH=/opt/node/bin:" in service


def test_install_refuses_without_a_library():
    with pytest.raises(ValueError, match="library"):
        install(Config())


def test_server_on_an_inherited_socket_and_idle_accounting(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    app = App(Config(musicbrainz=False), tmp_path)
    srv = app.make_server(sock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = sock.getsockname()[1]
    assert httpx.get(f"http://127.0.0.1:{port}/api/state").json()["albums"] == []
    assert app.idle_for() < 1
    srv.shutdown()
    srv.server_close()


def test_restart_refuses_while_a_job_runs(monkeypatch):
    import subprocess

    import noaap.systemd as sd

    monkeypatch.setattr(sd, "busy", lambda port=None: True)
    calls = []
    monkeypatch.setattr(sd, "systemctl", lambda *a: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    with pytest.raises(Refused, match="job is running"):
        sd.restart()
    assert calls == []
    sd.restart(force=True)  # only on purpose
    assert calls == [("restart", "noaap.service")]


def test_installed_port_is_read_from_the_unit(tmp_path, monkeypatch):
    import noaap.systemd as sd

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "systemd" / "user").mkdir(parents=True)
    (tmp_path / "systemd" / "user" / "noaap.socket").write_text("[Socket]\nListenStream=127.0.0.1:9123\n")
    assert sd.installed_port() == 9123


# -- flags that only describe the units ---------------------------------------------------


@pytest.mark.parametrize("action", ["restart", "status", "uninstall"])
@pytest.mark.parametrize("flag", [["--port", "9000"], ["--idle-exit", "60"]])
def test_a_flag_that_cannot_work_is_refused_not_ignored(action, flag, capsys, monkeypatch, tmp_path):
    """`service restart --port 9000` did nothing with the port — and said nothing either."""
    import noaap.cli as cli
    import noaap.systemd as sd

    monkeypatch.setattr(sd, "restart", lambda force=False: pytest.fail("must not act"))
    monkeypatch.setattr(sd, "uninstall", lambda: pytest.fail("must not act"))
    monkeypatch.setattr(sd, "status", lambda: pytest.fail("must not act"))
    assert cli.main(["service", action, *flag]) == 2
    message = capsys.readouterr().err
    assert flag[0] in message and "install" in message


def test_install_still_takes_both_flags(monkeypatch, capsys, tmp_path):
    import noaap.cli as cli
    import noaap.systemd as sd

    seen = {}
    monkeypatch.setattr(sd, "install",
                        lambda cfg, port, idle, checkout=False: seen.update(port=port, idle=idle,
                                                                            checkout=checkout) or [])
    monkeypatch.setattr(sd, "status", lambda: "")
    assert cli.main(["service", "install", "--port", "9000", "--idle-exit", "60"]) == 0
    assert seen == {"port": 9000, "idle": 60, "checkout": False}

    seen.clear()
    assert cli.main(["service", "install"]) == 0
    # the documented defaults, unchanged — and a unit that follows the tree is asked for, never assumed
    assert seen == {"port": 8765, "idle": 900, "checkout": False}

    seen.clear()
    assert cli.main(["service", "install", "--from-checkout"]) == 0
    assert seen["checkout"] is True

    # and it means nothing for the other actions, which do not write the units
    assert cli.main(["service", "restart", "--from-checkout"]) == 2


# -- and what ytalbum left on the machine (§9, slice 52) -----------------------------------


@pytest.fixture
def units(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = tmp_path / "systemd" / "user"
    directory.mkdir(parents=True)
    return directory


def theirs(units, port=8765):
    path = units / "ytalbum.socket"
    path.write_text(f"[Socket]\nListenStream=127.0.0.1:{port}\n")
    (units / "ytalbum.service").write_text("[Service]\nExecStart=/somewhere/ytalbum serve\n")
    return path


def test_the_same_port_is_a_sentence_not_an_address_already_in_use(units, monkeypatch):
    """systemd would answer from inside `enable --now`, which reads as a bug in noaap."""
    import subprocess

    import noaap.systemd as sd

    theirs(units, port=8765)
    monkeypatch.setattr(sd, "systemctl",
                        lambda *a: subprocess.CompletedProcess(a, 0, "active\n", ""))

    with pytest.raises(Refused, match="ytalbum is already listening on port 8765"):
        sd.install(Config(library_root=Path("/tmp")), port=8765)

    assert not (units / "noaap.socket").exists(), "nothing was written before the refusal"


def test_another_port_is_fine_and_so_is_a_stopped_ytalbum(units, monkeypatch):
    import subprocess

    import noaap.systemd as sd

    theirs(units, port=8765)
    monkeypatch.setattr(sd, "systemctl",
                        lambda *a: subprocess.CompletedProcess(a, 0, "active\n", ""))
    assert sd.clash(8766) is None, "a different port never clashes"

    monkeypatch.setattr(sd, "systemctl",
                        lambda *a: subprocess.CompletedProcess(a, 0, "inactive\n", ""))
    assert sd.clash(8765) is None, "an installed but stopped socket holds nothing"


def test_ytalbums_units_are_reported_and_never_removed(units, monkeypatch):
    import subprocess

    import noaap.systemd as sd

    theirs(units)
    monkeypatch.setattr(sd, "systemctl",
                        lambda *a: subprocess.CompletedProcess(a, 0, "inactive\n", ""))

    assert [p.name for p in sd.legacy_units()] == ["ytalbum.socket", "ytalbum.service"]
    assert "ytalbum's units are still here (2)" in sd.status()

    sd.install(Config(library_root=Path("/tmp")), port=8766)
    sd.uninstall()

    assert sd.legacy_units(), "install and uninstall leave ytalbum's alone"


# -- the unit runs a release, not the working tree (§9, slice 93) -----------------------------------


def test_the_unit_names_the_release_venv_when_there_is_one(tmp_path, monkeypatch):
    """An editable install points at a checkout, and this service restarts itself on demand — so the
    unit has to name a venv holding a released wheel, or an idle window is enough to run a half-edited
    tree as the user's app."""
    from noaap import systemd

    release = tmp_path / "noaap-release"
    (release / "bin").mkdir(parents=True)
    (release / "bin" / "noaap").write_text("#!/bin/sh\n")
    monkeypatch.setattr(systemd, "RELEASE_VENV", release)

    unit = render_units(Config(library_root=tmp_path))["noaap.service"]
    assert f"ExecStart={release / 'bin' / 'noaap'} serve --idle-exit 900" in unit
    # the watcher restarts every 30 s, so it must not follow the tree either
    watch = systemd.render_watch_unit(Config(library_root=tmp_path))["noaap-watch.service"]
    assert f"ExecStart={release / 'bin' / 'noaap'} watch" in watch


def test_without_a_release_venv_the_unit_runs_this_one(tmp_path, monkeypatch):
    import sys

    from noaap import systemd

    monkeypatch.setattr(systemd, "RELEASE_VENV", tmp_path / "not-installed")
    unit = render_units(Config(library_root=tmp_path))["noaap.service"]
    assert f"ExecStart={Path(sys.prefix) / 'bin' / 'noaap'} serve" in unit


def test_a_checkout_unit_is_asked_for_deliberately(tmp_path, monkeypatch):
    """`--from-checkout` is the way back for development: my own verification wants a unit that
    follows the tree, and the user's service must never be one by accident."""
    import sys

    from noaap import systemd

    release = tmp_path / "noaap-release"
    (release / "bin").mkdir(parents=True)
    (release / "bin" / "noaap").write_text("#!/bin/sh\n")
    monkeypatch.setattr(systemd, "RELEASE_VENV", release)

    unit = render_units(Config(library_root=tmp_path), from_checkout=True)["noaap.service"]
    assert f"ExecStart={Path(sys.prefix) / 'bin' / 'noaap'} serve" in unit
    assert str(release) not in unit


def test_install_says_which_noaap_the_service_will_run(tmp_path, monkeypatch):
    from noaap import systemd

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(systemd, "RELEASE_VENV", tmp_path / "not-installed")
    monkeypatch.setattr(systemd, "systemctl",
                        lambda *a: __import__("subprocess").CompletedProcess(a, 0, "", ""))

    said = "\n".join(systemd.install(Config(library_root=tmp_path)))

    assert "no release install at" in said and "follows this checkout" in said
