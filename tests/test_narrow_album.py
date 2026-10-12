"""The album's track table fits a narrow window (DESIGN §9, slice 186; noted in R-580).

At 500 px the album panel was 631 px wide and the whole page scrolled sideways: the trim cell held
its two inputs side by side, the ✓♪✕ cell would not wrap, and the "from" header stayed when its
column went. Rows may get taller; the page may not get wider.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
from test_web import library, opus_template

WEBUI = Path(__file__).parent.parent / "src/noaap/webui"


def narrow_rules() -> dict[str, str]:
    """{selector: body} of every rule inside a `@media (max-width: 40rem)` block."""
    css = re.sub(r"/\*.*?\*/", "", (WEBUI / "style.css").read_text(encoding="utf-8"), flags=re.S)
    rules: dict[str, str] = {}
    for m in re.finditer(r"@media \(max-width: 40rem\) \{", css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css[m.end():i - 1]):
            for one in selector.split(","):
                rules[" ".join(one.split())] = rules.get(" ".join(one.split()), "") + body
    return rules


def test_the_trim_cell_stacks_its_inputs_in_a_narrow_window():
    assert re.search(r"flex-wrap\s*:\s*wrap", narrow_rules().get("#album td.trim .cell-row", "")), narrow_rules()


def test_the_actions_cell_wraps_in_a_narrow_window():
    assert re.search(r"white-space\s*:\s*normal", narrow_rules().get("#album td.actions-cell", "")), narrow_rules()


def test_the_from_header_goes_with_its_column():
    js = (WEBUI / "app.js").read_text(encoding="utf-8")
    assert re.search(r'h\("th", \{ class: "src" \}, "from"\)', js), "the header of the hidden column hides with it"


# -- measured in a real browser, where one is installed -------------------------------------------


@pytest.mark.skipif(not os.environ.get("NOAAP_BROWSER") or not shutil.which("google-chrome"),
                    reason="set NOAAP_BROWSER=1 (and have google-chrome) to measure in a real browser")
@pytest.mark.parametrize("width", [640, 500])
def test_no_sideways_scroll_with_an_album_open(library, opus_template, width):
    import httpx
    import websockets
    from test_incremental import FakeYouTube

    from noaap.config import Config
    from noaap.service import Service
    from noaap.web import App

    yt = FakeYouTube(opus_template)
    app = App(Config(musicbrainz=False), library, port=0,
              service_factory=lambda job: Service(Config(musicbrainz=False), library, log=job.log.append, yt=yt))
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    profile = tempfile.mkdtemp(prefix="noaap-chrome-")
    port = 9400 + width % 97
    chrome = subprocess.Popen(["google-chrome", "--headless=new", f"--user-data-dir={profile}", "--mute-audio",
                               "--disable-gpu", f"--remote-debugging-port={port}", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def measure() -> dict:
        for _ in range(100):
            try:
                tabs = httpx.get(f"http://127.0.0.1:{port}/json").json()
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
        async with websockets.connect(url, max_size=None) as ws:
            n = 0

            async def cmd(method, **params):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == n:
                        return m.get("result", {})

            async def ev(expr):
                r = await cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
                return r.get("result", {}).get("value")

            async def until(expr, seconds=20.0):
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    if await ev(expr):
                        return True
                    await asyncio.sleep(0.1)
                return False

            await cmd("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=False)
            await cmd("Page.navigate", url=base)
            assert await until("document.querySelector('#grid .card') !== null")
            x, y = await ev("""(() => { const c = document.querySelector('#grid .card'); c.scrollIntoView();
                               const r = c.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 3]; })()""")
            for kind in ("mousePressed", "mouseReleased"):
                await cmd("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)
            assert await until("!document.querySelector('#album').hidden && "
                               "document.querySelectorAll('#album tbody tr[data-id]').length > 0")
            await asyncio.sleep(0.5)
            return await ev("""({scroll: document.documentElement.scrollWidth, window: innerWidth,
                                 headers: [...document.querySelectorAll('#album thead th')]
                                   .filter(t => getComputedStyle(t).display !== 'none').length,
                                 cells: [...document.querySelector('#album tbody tr[data-id]').children]
                                   .filter(t => getComputedStyle(t).display !== 'none').length})""")

    try:
        got = asyncio.run(measure())
    finally:
        chrome.terminate()
        chrome.wait(10)
        shutil.rmtree(profile, ignore_errors=True)
        srv.shutdown()

    assert got["scroll"] <= got["window"], got
    assert got["headers"] == got["cells"], f"a header for every column that is shown: {got}"
