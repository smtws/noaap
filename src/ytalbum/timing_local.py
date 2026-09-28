"""The local timing provider: separate the voice, then force-align the words to it (§9, slice 36).

Imported only when `timing_provider = "local"`, and it is the only file in ytalbum that touches a
model. It needs the optional extra:

    uv pip install "ytalbum[timing]"          # torch, torchaudio, demucs

and on a machine without a usable GPU, the smaller CPU build of torch is worth asking for first
(see the README). What it downloads on first use, into torch's own cache:

    wav2vec2 aligner, English   361 MB   torchaudio WAV2VEC2_ASR_BASE_960H
    wav2vec2 aligner, German    361 MB   torchaudio VOXPOPULI_ASR_BASE_10K_DE
    Demucs htdemucs              81 MB   the vocal separation

**No Whisper.** The spike measured both and the 3 GB model is the worse aligner here
(`docs/spikes/2026-09-alignment.md`): wav2vec2 CTC over a separated vocal stem placed 17 of 20 real
tracks within a median of 0.94 s per line, with no penalty for growled vocals or for German.
Separation is not a refinement but the precondition — the same aligner on the mixed track is out by
a median of 12 seconds, because CTC has nothing to hold on to during an instrumental passage.
"""

from __future__ import annotations

import logging
import re
import statistics
import unicodedata
import warnings
from collections.abc import Callable
from pathlib import Path

from .timing import (
    ALIGN,
    TRANSCRIBE,
    VERIFY_LOST,
    VERIFY_THRESHOLD,
    Timed,
    TimedLine,
    TimingUnavailable,
    language_of,
    release_gpu_memory,
    signals_of,
    verified,
)

log = logging.getLogger(__name__)

# torchaudio's own pipelines, which is all the spike's best arm ever used
BUNDLES = {"en": "WAV2VEC2_ASR_BASE_960H", "de": "VOXPOPULI_ASR_BASE_10K_DE"}
SEPARATOR = "htdemucs"
BLANK = 0  # index 0 is the CTC blank in both bundles (their label 0 is "-"), never a letter
MISSING = ("the local timing provider needs the optional extra: "
           'uv pip install "ytalbum[timing]" (torch, torchaudio, demucs)')
# the second extra: a Whisper decoder, for the cross-check and for drafting words (§9, slice 38)
WHISPER = "large-v3"
WHISPER_SIZE = "3.09 GB on first use"
NO_CHECK = ('the second opinion needs the other extra: uv pip install "ytalbum[timing-check]" '
            f"(faster-whisper and stable-ts; {WHISPER_SIZE})")
CUDA_FULL = ("the graphics card has no room left for the second opinion beside the aligner and the "
             "separator; checking on the processor instead, which is slower but gives the same answer")
CUDA_TRAP = ("ctranslate2 wants CUDA 12's libcublas and the installed torch brought a different one; "
             "falling back to the processor. Install nvidia-cublas-cu12 and nvidia-cudnn-cu12 beside "
             "it, or set timing_device = \"cpu\" and forget about it")


class LocalTiming:
    """Alignment on this machine. Nothing is sent anywhere; nothing is imported until asked."""

    name = "local"

    def __init__(self, device: str = "auto", log: Callable[[str], None] | None = None,
                 verify: bool | None = None, threshold: float = VERIFY_THRESHOLD,
                 lost: float = VERIFY_LOST) -> None:
        self.device = device
        self.log = log or (lambda _: None)
        # None means "check when the second aligner is installed", which is what the extra is for
        self.verify = verify
        self.threshold = threshold
        self.lost = lost
        self._models: dict[str, object] = {}
        self._separator: object | None = None
        self._whisper: object | None = None

    # -- what it can do ----------------------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        """Align always; derive words only with the second extra, which carries the big model."""
        try:
            import torch  # noqa: F401
            import torchaudio  # noqa: F401
        except ImportError:
            return frozenset()
        return frozenset({ALIGN, TRANSCRIBE}) if has_whisper() else frozenset({ALIGN})

    def verifying(self) -> bool:
        """Whether an alignment is checked against the second method (§9, slice 38)."""
        return has_whisper() if self.verify is None else bool(self.verify and has_whisper())

    def resolved_device(self) -> str:
        import torch

        if self.device in ("cpu", "cuda"):
            return self.device
        return "cuda" if torch.cuda.is_available() else "cpu"

    # -- the work ----------------------------------------------------------------------

    def align(self, audio: Path, lines: list[str], *, language: str | None = None,
              check: Callable[[], None] | None = None) -> Timed:
        try:
            import torch
            import torchaudio
            import torchaudio.functional as AF
        except ImportError as e:
            raise TimingUnavailable(MISSING) from e

        words = [line for line in lines if line.strip()]
        if not words:
            raise TimingUnavailable("there are no words to place")
        lang = language or language_of(lines)
        if lang not in BUNDLES:
            raise TimingUnavailable(f"no aligner for {lang!r} — this provider has {', '.join(BUNDLES)}")
        device = self.resolved_device()

        if check:
            check()
        wave, rate = self._vocals(audio, device, check)
        if device == "cuda":
            # the separator's weights and the aligner's do not both fit comfortably in 8 GB
            torch.cuda.empty_cache()
        if check:
            check()

        bundle = getattr(torchaudio.pipelines, BUNDLES[lang])
        model = self._aligner(bundle, lang, device)
        if rate != bundle.sample_rate:
            wave = AF.resample(wave, rate, bundle.sample_rate)
        with torch.inference_mode():
            emission, _ = model(wave.to(device))
            emission = torch.log_softmax(emission, dim=-1)
        if check:
            check()

        labels = {c: i for i, c in enumerate(bundle.get_labels())}
        owners, targets, first_token = [], [], []
        for i, line in enumerate(lines):
            for word in line.split():
                ids = _label_ids(word, labels)
                if not ids:
                    continue  # a word of punctuation or an alphabet this model does not have
                owners.append(i)
                first_token.append(len(targets))
                targets.extend(ids)
                targets.append(labels["|"])
        if targets and targets[-1] == labels["|"]:
            targets.pop()
        if not targets:
            raise TimingUnavailable("none of these words can be written in the aligner's alphabet")

        tokens = torch.tensor([targets], dtype=torch.int32, device=device)
        aligned, scores = AF.forced_align(emission, tokens, blank=0)
        spans = AF.merge_tokens(aligned[0], scores[0].exp())
        seconds = wave.size(1) / emission.size(1) / bundle.sample_rate

        starts: dict[int, float] = {}
        ends: dict[int, float] = {}
        scored: dict[int, list[float]] = {}
        for w, token in enumerate(first_token):
            if token >= len(spans):
                break
            starts.setdefault(owners[w], round(spans[token].start * seconds, 2))
            last = min(token + 1, len(spans) - 1)
            ends[owners[w]] = round(spans[last].end * seconds, 2)
            # the aligner's own confidence in this word, which it has already computed (§9, slice 44)
            scored.setdefault(owners[w], []).append(float(spans[token].score))
        placed = [TimedLine(text=line, start=starts.get(i), end=ends.get(i)) for i, line in enumerate(lines)]
        timed = Timed(lines=placed, provider=self.name, model=f"{BUNDLES[lang]} + {SEPARATOR}",
                      version=torchaudio.__version__,
                      parameters={"device": device, "language": lang, "separated": "vocals"})
        per_line = [statistics.median(v) for v in scored.values() if v]
        if per_line:
            timed.parameters["confidence"] = format(statistics.median(per_line), ".3f")
        # What this answer says about itself, always — not only when a second method is checking it
        # (§9, slice 44). It costs one pass over a waveform that is already in hand, and it is what decides
        # whether an lrclib entry's words belong to this recording (§9, slice 46).
        sung = sung_stretches(wave.cpu(), bundle.sample_rate)
        length = wave.size(1) / bundle.sample_rate
        mine = signals_of([line.start for line in timed.lines], length=length, sung=sung,
                          failed=len(timed.unplaced), confidence=_number(timed.parameters.get("confidence")))
        timed.parameters.update(mine.to_parameters("own"))
        if not self.verifying():
            return timed
        # The second opinion hears the **mixed** track, not the stem the CTC pass needs (§9, slice 38).
        # Measured, not assumed: on the stem it placed 11 of 42 lines nowhere and the rest 20 s
        # early; on the same track's mix it landed within 0.7 s of a hand-checked sidecar. The
        # different front end is also what makes it a second opinion rather than a second pass.
        if check:
            check()
        if device == "cuda":
            torch.cuda.empty_cache()  # the aligner and the separator are still holding VRAM
        try:
            second = self._whisper_align(audio, lines, lang)
        except (TimingUnavailable, RuntimeError, OSError) as e:
            # the check is an extra; losing it must not lose the alignment the user waited for
            self.log(f"the second opinion could not run: {e}")
            timed.parameters["unchecked"] = str(e)[:200]
            return timed
        finally:
            # Whisper is let go again on a graphics card, because the *next* track's separation needs
            # the room: keeping it resident is what made track two of the verification run die with
            # "tried to allocate 1.34 GiB" on this 8 GB card. Reloading it costs seconds off a warm
            # disk, and on the processor there is nothing to compete for, so it stays.
            if self._whisper_device == "cuda":
                self._free_vram()
        # the primary's own signals are already measured above; the second method's are not
        evidence = (mine,
                    signals_of([line.start for line in second.lines], length=length, sung=sung,
                               failed=int(second.parameters.get("failed", 0) or 0),
                               confidence=_number(second.parameters.get("confidence"))))
        return verified(timed, second, self.threshold, self.lost, evidence=evidence)

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed:
        """What this machine hears, with the second extra installed. A draft, and labelled as one.

        The spike measured this as the weaker half of the job — three quarters of a clean song's
        lines, half of a harsh one's — which is why it is offered only where a track has no words at
        all (§9, slice 37) and why the page calls it a guess.
        """
        if not has_whisper():
            raise TimingUnavailable(
                "this provider places words it is given; deriving them needs the bigger model. "
                + NO_CHECK)
        if check:
            check()
        if self.resolved_device() == "cuda":
            # whatever an earlier alignment left reserved would otherwise push this onto the
            # processor, where it is 30× slower and only a log line says why
            import torch

            torch.cuda.empty_cache()
        self.log(f"listening to {audio.name} with {WHISPER} …")
        result = self._whisper_run(
            lambda model: model.transcribe(str(audio), language=language, temperature=0, verbose=None))
        if check:
            check()
        lines = [TimedLine(text=segment.text.strip(), start=round(segment.start, 2),
                           end=round(segment.end, 2))
                 for segment in result.segments if segment.text.strip()]
        return Timed(lines=lines, provider=self.name, model=WHISPER, version=_whisper_version(),
                     parameters={"device": self._whisper_device, "language": language or "detected",
                                 "temperature": "0"})

    def release(self) -> bool:
        """Let go of every model this provider has loaded (§9, slice 41, backlog 18).

        For the web service this is barely needed — a provider is built per job and dropped with it —
        but `ytalbum timing-serve` keeps one for the life of the process, and a machine that is asked
        to align one track an hour should not hold 3 GB of a graphics card for the other fifty-nine
        minutes. The next request loads them again, in seconds off a warm disk, and says so in its log.
        """
        held = bool(self._models or self._separator or self._whisper)
        self._models, self._separator, self._whisper = {}, None, None
        if held:
            self.log("let go of the models; the next request loads them again")
        release_gpu_memory()
        return held

    # -- the second opinion ----------------------------------------------------------------

    def _whisper_align(self, audio: Path, lines: list[str], lang: str) -> Timed:
        """The same words, placed by a Whisper decoder hearing the mixed track (§9, slice 38)."""
        self.log(f"checking the alignment against {WHISPER} …")
        # it reports the segments it could not place as a warning and nowhere else, so that is
        # where the count has to come from (§9, slice 44)
        with warnings.catch_warnings(record=True) as said:
            warnings.simplefilter("always")
            result = self._whisper_run(
                lambda model: model.align(str(audio), "\n".join(lines), language=lang, original_split=True))
        failed = 0
        for one in said:
            if m := re.search(r"(\d+)/(\d+) segments? failed to align", str(one.message)):
                failed = int(m[1])
        placed = [TimedLine(text=segment.text.strip(), start=round(segment.start, 2))
                  for segment in result.segments]
        if len(placed) != len(lines):
            # it merged or split lines: there is nothing to compare line by line, so it abstains
            # rather than shifting everything by one (this happened on 1 of 20 tracks in the spike)
            self.log(f"the second method returned {len(placed)} lines for {len(lines)} — not comparing")
            placed = [TimedLine(text=line, start=None) for line in lines]
        out = Timed(lines=placed, provider=self.name, model=WHISPER, version=_whisper_version(),
                    parameters={"failed": str(failed)})
        chances = [float(w.probability) for segment in result.segments
                   for w in (getattr(segment, "words", None) or []) if getattr(w, "probability", None)]
        if chances:
            out.parameters["confidence"] = format(statistics.median(chances), ".3f")
        return out

    _whisper_device = "cpu"
    _whisper_cpu_only = False

    def _whisper_run(self, call: Callable[[object], object]):
        """Run one inference, and survive the CUDA trap wherever it decides to fire.

        ctranslate2 does not touch a CUDA library until the first inference, so loading the model on
        the GPU succeeds and `libcublas.so.12 is not found` arrives later — which is exactly what
        happened on the machine this was written on (torch with CUDA 13, ctranslate2 built for 12).
        Catching it only at load time therefore caught nothing. One retry on the processor is the
        whole recovery: it is slower, it is local, and it costs nothing but time.
        """
        try:
            return call(self._whisper_model())
        except (RuntimeError, OSError) as e:
            if self._whisper_cpu_only or self._whisper_device != "cuda":
                raise
            if _is_cuda_library_trap(e):
                self.log(CUDA_TRAP)
            elif _is_out_of_memory(e):
                self.log(CUDA_FULL)
            else:
                raise
            self._whisper, self._whisper_cpu_only = None, True
            self._free_vram()
            return call(self._whisper_model())

    def _free_vram(self) -> None:
        import gc

        import torch

        self._whisper = None
        gc.collect()  # ctranslate2's memory is not torch's: it goes when the object is collected
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _whisper_model(self):
        """Loaded once, and it comes down the wire the first time: 3.09 GB."""
        if self._whisper is not None:
            return self._whisper
        try:
            import stable_whisper
        except ImportError as e:
            raise TimingUnavailable(NO_CHECK) from e
        device = "cpu" if self._whisper_cpu_only else self.resolved_device()
        self.log(f"loading {WHISPER} for the second opinion ({WHISPER_SIZE})")
        try:
            self._whisper = stable_whisper.load_faster_whisper(
                WHISPER, device=device, compute_type="float16" if device == "cuda" else "int8")
            self._whisper_device = device
        except (RuntimeError, OSError) as e:
            if device == "cuda" and _is_cuda_library_trap(e):
                # the spike hit exactly this: torch shipped CUDA 13, ctranslate2 wanted 12
                self.log(f"{CUDA_TRAP}")
                self._whisper = stable_whisper.load_faster_whisper(WHISPER, device="cpu", compute_type="int8")
                self._whisper_device = "cpu"
            else:
                raise TimingUnavailable(f"{WHISPER} could not be loaded: {e}") from e
        return self._whisper

    # -- the two models ------------------------------------------------------------------

    def _aligner(self, bundle: object, lang: str, device: str):
        if lang not in self._models:
            self.log(f"loading the {lang} aligner ({BUNDLES[lang]}, 361 MB on first use)")
            self._models[lang] = bundle.get_model().to(device).eval()  # type: ignore[attr-defined]
        return self._models[lang]

    def _vocals(self, audio: Path, device: str, check: Callable[[], None] | None):
        """The voice alone, as one mono waveform. This is what makes the alignment work."""
        try:
            from demucs.api import Separator
        except ImportError as e:
            raise TimingUnavailable(MISSING) from e

        # held in a local as well as in the cache: `release()` may empty the cache from another
        # thread between these lines, and a separation that loses its separator half way through is
        # the kind of failure that looks like a bug in the model (it happened, `docs/qa-catalog.md`
        # section AB)
        separator = self._separator
        if separator is None:
            self.log(f"loading the separator ({SEPARATOR}, 81 MB on first use)")
            separator = self._separator = Separator(model=SEPARATOR, device=device, progress=False)
        if check:
            check()
        self.log(f"separating the voice from {audio.name} …")
        _, stems = separator.separate_audio_file(audio)  # type: ignore[attr-defined]
        vocals = stems["vocals"].mean(0, keepdim=True).cpu()
        return vocals, int(separator.samplerate)  # type: ignore[attr-defined]


def separated_voice(audio: Path, into: Path, log: Callable[[str], None] | None = None,
                    check: Callable[[], None] | None = None) -> Path | None:
    """Write the track's isolated voice to `into`, or return None if that cannot be done (§9, slice 45).

    A transcriber hears far more of a song when the band is taken off it. Measured on one real
    track against its own published lyric (`docs/qa-catalog.md`, section AF): Deepgram found 16 of
    52 lines on the mix and **32** on the voice; the local decoder 28 and **38**. It is also less of
    the recording to send anywhere, which matters when the transcriber is somebody else's computer.

    Never fatal: without the `ytalbum[timing]` extra, or if anything goes wrong, the caller falls
    back to the mixed track, which is what it always sent.
    """
    say = log or (lambda _: None)
    try:
        import soundfile
        import torch  # noqa: F401
    except ImportError:
        return None
    engine = LocalTiming(log=say)
    try:
        wave, rate = engine._vocals(audio, engine.resolved_device(), check)
        soundfile.write(into, wave[0].cpu().numpy(), rate)
    except (TimingUnavailable, RuntimeError, OSError) as e:
        say(f"could not separate the voice ({e}); listening to the mixed track instead")
        return None
    finally:
        engine.release()
    return into


def sung_stretches(wave, rate: int, frame: float = 0.1, bridge: float = 0.4,
                   floor: float = 0.06) -> list[tuple[float, float]]:
    """When somebody is singing, from the separated vocal the aligner already made (§9, slice 44).

    The separation is the expensive part and it is already done, so this costs nothing: chop the
    stem into tenths of a second, take each one's RMS, and call it singing where it rises above a
    fraction of the track's own loud level (relative, because one track is mastered quieter than
    another). Stretches less than `bridge` apart are joined, since a breath between two lines is not
    the end of the singing.
    """
    import torch

    audio = wave[0] if wave.dim() > 1 else wave
    step = max(1, int(frame * rate))
    if audio.numel() < step:
        return []
    frames = audio[: audio.numel() // step * step].reshape(-1, step)
    rms = frames.float().pow(2).mean(dim=1).sqrt()
    loud = torch.quantile(rms, 0.95)
    if not float(loud):
        return []
    on = (rms > loud * floor).tolist()
    out: list[tuple[float, float]] = []
    for i, singing in enumerate(on):
        if not singing:
            continue
        at = i * frame
        if out and at - out[-1][1] <= bridge:
            out[-1] = (out[-1][0], at + frame)
        else:
            out.append((at, at + frame))
    return out


def _number(text: str | None) -> float | None:
    """A parameter that is a number, or nothing — parameters are strings, signals are not."""
    try:
        return float(text) if text else None
    except ValueError:
        return None


def has_whisper() -> bool:
    """Whether the second extra is installed. Asked, never assumed: it is 3 GB of model."""
    from importlib.util import find_spec

    return find_spec("stable_whisper") is not None and find_spec("faster_whisper") is not None


def _whisper_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"faster-whisper {version('faster-whisper')}"
    except PackageNotFoundError:
        return ""


def _is_out_of_memory(error: Exception) -> bool:
    """Not a bug in either model: 8 GB of VRAM does not hold the aligner, the separator and 3 GB of
    Whisper at once, which is what this laptop measured (`docs/qa-catalog.md`, section Y)."""
    return "out of memory" in str(error).lower()


def _is_cuda_library_trap(error: Exception) -> bool:
    """The `libcublas.so.12 is not found` family, which is a packaging problem, not a real failure."""
    text = str(error).lower()
    return any(name in text for name in ("libcublas", "libcudnn", "cuda driver", "cublas"))


def _label_ids(word: str, labels: dict[str, int]) -> list[int]:
    """One word in the aligner's alphabet, dropping only what it cannot write at all.

    The two bundles do not agree on case or accents: the English one holds `A-Z`, an apostrophe and
    `|`, the German one holds `a-z` **with** `ä ö ü ß`. So each character is tried as it is, then in
    both cases, and only then with its accent stripped — "Bösewicht" keeps its ö for the German
    aligner and would fall back to BOSEWICHT for one that has none. A character with no candidate is
    skipped rather than allowed to drop its word, because a dropped word takes its line's stamp
    with it.
    """
    out = []
    for ch in word:
        bare = unicodedata.normalize("NFD", ch)[0]
        for candidate in (ch, ch.upper(), ch.lower(), bare.upper(), bare.lower()):
            i = labels.get(candidate)
            if i is not None and i != BLANK:
                out.append(i)
                break
    return out
