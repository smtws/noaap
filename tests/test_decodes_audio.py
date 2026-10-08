"""The transcriber can actually read a file (DESIGN §9, slice 161; R-542).

The user's first local draft on 1.48.0 died with
`TypeError: open() got an unexpected keyword argument 'metadata_errors'`, before a single word was
heard. faster-whisper 1.2.1 — the newest there is — calls `av.open(..., metadata_errors=…)`, and
PyAV dropped that argument in 19.0. The release install had av 19.0.0, the checkout had 18.1.0, so
every measurement I made ran on a venv that could decode and the release never ran this path at all.

This is the cheapest possible case for it: hand faster-whisper's own decoder a second of sound. It
needs no model, no GPU and no network, and it fails on exactly the mismatch that shipped.
"""

from __future__ import annotations

import subprocess

import pytest

faster_whisper = pytest.importorskip("faster_whisper",
                                     reason="the timing-check extra is not installed")


@pytest.fixture
def a_second_of_wav(tmp_path):
    if not (ffmpeg := __import__("shutil").which("ffmpeg")):
        pytest.skip("ffmpeg is not installed")
    path = tmp_path / "tone.wav"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", str(path)], check=True)
    return path


def test_faster_whisper_can_decode_a_file(a_second_of_wav):
    """What the draft does first, and what no test did before: read the audio."""
    from faster_whisper.audio import decode_audio

    samples = decode_audio(str(a_second_of_wav))

    assert len(samples) > 8000, f"a second at 16 kHz, got {len(samples)} samples"


def test_the_pyav_version_is_one_faster_whisper_can_call():
    """The constraint, stated where a failing install will say it plainly. If faster-whisper ever
    stops passing `metadata_errors`, this is the case to delete along with the pin."""
    import av

    major = int(str(av.__version__).split(".")[0])
    assert major < 19, (
        f"PyAV {av.__version__} dropped `metadata_errors`, which faster-whisper "
        f"{faster_whisper.__version__} still passes — see pyproject's timing-check extra")
