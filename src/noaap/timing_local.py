"""The local timing provider: separate the voice, then force-align the words to it (§9, slice 36).

Imported only when `timing_provider = "local"`, and it is the only file in noaap that touches a
model. It needs the optional extra:

    uv pip install "noaap[timing]"          # torch, torchaudio, demucs

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
import os
import re
import statistics
import sys
import unicodedata
import warnings
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .timing import (
    ALIGN,
    LISTEN,
    TRANSCRIBE,
    VERIFY_LOST,
    VERIFY_THRESHOLD,
    Heard,
    Timed,
    TimedLine,
    TimingUnavailable,
    heard_from,
    language_of,
    release_gpu_memory,
    signals_of,
    unplace_unsupported,
    verified,
)

log = logging.getLogger(__name__)

# torchaudio's own pipelines, which is all the spike's best arm ever used
BUNDLES = {"en": "WAV2VEC2_ASR_BASE_960H", "de": "VOXPOPULI_ASR_BASE_10K_DE"}
SEPARATOR = "htdemucs"
BLANK = 0  # index 0 is the CTC blank in both bundles (their label 0 is "-"), never a letter
MISSING = ("the local timing provider needs the optional extra: "
           'uv pip install "noaap[timing]" (torch, torchaudio, demucs)')
# the second extra: a Whisper decoder, for the cross-check and for drafting words (§9, slice 38)
WHISPER = "large-v3"
WHISPER_SIZE = "3.09 GB on first use"
NO_CHECK = ('the second opinion needs the other extra: uv pip install "noaap[timing-check]" '
            f"(faster-whisper and stable-ts; {WHISPER_SIZE})")
# What one job needs on the card, in MiB, measured on this laptop's 8 GB card (the numbers and how
# they were taken are in `timing.py`): a four-minute alignment peaked at 3460 MiB reserved, and the
# second opinion's own model holds 3616 MiB of its own — outside torch's pool, which is why nothing
# but dropping it gives that back. A card with less room than this gets the job on the processor
# instead: measured at 11.4× the time for the same track, which is slow but is an answer (§9, slice 82).
CARD_NEEDS = {ALIGN: 3500, TRANSCRIBE: 3800, LISTEN: 3800}
#: which of this provider's models each kind of job actually touches (§9, slice 84). What a job will
#: not touch is let go **before** the card is asked for room: a held model is room for the job that
#: reuses it and an obstacle to the job that does not. Measured the hard way — after a `listen` the
#: process held 3856 MiB of large-v3, the gate counted it as room, and the alignment that followed
#: ran out of memory half way through (R-284).
USES = {ALIGN: ("aligner", "separator"), LISTEN: ("listener",), TRANSCRIBE: ("listener",)}
CARD_SLOWER = 11        # times as long on the processor, measured on the same track
WHISPER_CARD = 3616     # MiB the second opinion holds once loaded, if this provider is holding it
CARD_RAN_OUT = ("the graphics card ran out of memory part way through, so this job has no answer and "
                "nothing was written. Try again when whatever else is using the card has finished, or "
                'set timing_device = "cpu" to stop using it altogether')
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
        # keyed by device as well as language: a run that had to move to the processor may not be
        # handed a model that lives on the card (§9, slice 82)
        self._models: dict[tuple[str, str], object] = {}
        self._separator: object | None = None
        self._separator_device: str | None = None
        self._whisper: object | None = None
        self._whisper_where: str | None = None   # where the next load should go, if not the default

    # -- what it can do ----------------------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        """Align always; derive words — and listen for given ones — only with the second extra.

        `listen` comes with the big model rather than with the aligner, because it *is* the big model
        (§9, slice 83): the words are matched to what it heard, and the matching lives in the core.
        """
        try:
            import torch  # noqa: F401
            import torchaudio  # noqa: F401
        except ImportError:
            return frozenset()
        return frozenset({ALIGN, TRANSCRIBE, LISTEN}) if has_whisper() else frozenset({ALIGN})

    def verifying(self) -> bool:
        """Whether an alignment is checked against the second method (§9, slice 38)."""
        return has_whisper() if self.verify is None else bool(self.verify and has_whisper())

    def resolved_device(self) -> str:
        import torch

        if self.device in ("cpu", "cuda"):
            return self.device
        return "cuda" if torch.cuda.is_available() else "cpu"

    def keep_only_what_it_uses(self, what: str) -> bool:
        """Let go of the models this job will not touch (§9, slice 84). True when anything went.

        **Held for the next job is not the same as held for this one.** A `listen` leaves 3.6 GB of
        the big model on the card; an alignment does not use a word of it, so counting it as room —
        which is right for the next `listen` — sent an alignment to the card with 3355 MiB free for a
        run that peaks at 3460 and killed it half way through (R-284). Two buttons pressed in turn is
        an ordinary thing for a person to do, so the room is made before the card is asked.
        """
        keep, freed = USES.get(what, ()), []
        if "listener" not in keep and self._whisper is not None:
            self._whisper = None
            freed.append("the big model")
        if "aligner" not in keep and (self._models or self._separator):
            self._models, self._separator, self._separator_device = {}, None, None
            freed.append("the aligner and the separator")
        if not freed:
            return False
        self.log(f"letting go of {' and '.join(freed)}: this job does not use "
                 + ("them" if len(freed) > 1 or "aligner" in freed[0] else "it"))
        release_gpu_memory()
        return True

    def device_now(self, what: str = ALIGN) -> str:
        """Where *this* job runs, asked now rather than when the settings were written (§9, slice 82).

        The card, unless there is not enough room on it at this moment — another program's window, a
        game, a second noaap. Then the processor, and the job says so in its first line, because the
        same track takes about eleven times as long there and somebody is waiting for it.

        **Asking makes room first** (§9, slice 84): whatever this job will not touch is let go before
        the question, so that what is left holding the card is only what this job reuses — and that
        much really is room rather than an obstacle.
        """
        try:
            device = self.resolved_device()
        except ImportError:
            return "cpu"   # no torch on this machine at all; the job itself says so in a moment
        if device != "cuda":
            return device
        self.keep_only_what_it_uses(what)
        needs = CARD_NEEDS.get(what, CARD_NEEDS[ALIGN])
        try:
            free, total = card_room()
            room = free + pool_held()
            if "listener" in USES.get(what, ()) and self._whisper is not None \
                    and self._whisper_device == "cuda":
                room += WHISPER_CARD   # loaded, and this job is the kind that reuses it
        except Exception:  # a torch too old to ask, or a driver that will not say: try the card
            log.debug("could not ask the card how much room it has", exc_info=True)
            return device
        if room >= needs:
            return device
        self.log(f"the graphics card has {free} MiB free of {total} and this needs about {needs} — "
                 f"running on the processor instead, which takes about {CARD_SLOWER}× as long")
        return "cpu"

    # -- the work ----------------------------------------------------------------------

    def align(self, audio: Path, lines: list[str], *, language: str | None = None,
              check: Callable[[], None] | None = None, device: str | None = None) -> Timed:
        """Place these words on this recording's clock.

        `device` is here so the caller can decide once, before it writes its own first line about the
        job (§9, slice 82); left out, this asks `device_now` itself, which is what the CLI and the
        timing server want.
        """
        device = device or self.device_now(ALIGN)
        try:
            return self._align(audio, lines, language=language, check=check, device=device)
        except Exception as e:
            # **one sentence, not a traceback** (§9, slice 82). Running out of memory half way
            # through is not a bug in either model and there is nothing in a stack trace for the
            # person reading the page; what this run held goes back, so the next attempt has room.
            if device == "cuda" and _is_out_of_memory(e):
                self.release()
                raise TimingUnavailable(CARD_RAN_OUT) from e
            raise

    def _align(self, audio: Path, lines: list[str], *, language: str | None,
               check: Callable[[], None] | None, device: str) -> Timed:
        try:
            import torch
            import torchaudio
            import torchaudio.functional as AF
        except ImportError as e:
            raise TimingUnavailable(MISSING) from e

        # the second opinion goes where the alignment went: a card with no room for one has none
        # for the other, and the check is not worth pushing the real work off it
        self._whisper_where = device
        words = [line for line in lines if line.strip()]
        if not words:
            raise TimingUnavailable("there are no words to place")
        lang = language or language_of(lines)
        if lang not in BUNDLES:
            raise TimingUnavailable(f"no aligner for {lang!r} — this provider has {', '.join(BUNDLES)}")

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
        by_line = {i: statistics.median(v) for i, v in scored.items() if v}
        self._last_line_scores = by_line        # what each line is worth, for whoever measures
        self._last_line_evidence = {i: {"score": by_line.get(i), "words": len(scored.get(i, [])),
                                        "start": starts.get(i), "end": ends.get(i)}
                                    for i in range(len(lines))}
        per_line = list(by_line.values())
        if per_line:
            timed.parameters["confidence"] = format(statistics.median(per_line), ".3f")
        # What this answer says about itself, always — not only when a second method is checking it
        # (§9, slice 44). It costs one pass over a waveform that is already in hand, and it is what decides
        # whether an lrclib entry's words belong to this recording (§9, slice 46).
        sung = sung_stretches(wave.cpu(), bundle.sample_rate)
        length = wave.size(1) / bundle.sample_rate
        # **what nothing supports is not placed** (§9, slice 81). Forced alignment puts every line
        # somewhere; this takes back the ones that were put where nobody sings, or faster than
        # anybody sings, and says so in the result.
        if taken := unplace_unsupported(timed, sung, by_line):
            timed.parameters["unsupported"] = "; ".join(taken)
            for said in taken:
                self.log(f"  not placed — {said}")
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
                   check: Callable[[], None] | None = None, device: str | None = None) -> Timed:
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
        # asked after that tidy-up, so a full card is a full card and not last job's leftovers
        self._whisper_where = device or self.device_now(TRANSCRIBE)
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

    def heard(self, audio: Path, *, language: str | None = None,
              check: Callable[[], None] | None = None, device: str | None = None) -> Heard:
        """What this machine heard, word by word, for `listen` to match given lines to (§9, slice 83).

        The same model and the same request as a draft — a transcript is a transcript — kept as words
        instead of grouped into lines, because the grouping is what a draft needs and what matching
        must not be given.
        """
        if not has_whisper():
            raise TimingUnavailable("listening for the words needs the bigger model. " + NO_CHECK)
        if check:
            check()
        self._whisper_where = device or self.device_now(TRANSCRIBE)
        self.log(f"listening to {audio.name} with {WHISPER} for the words you gave")
        result = self._whisper_run(
            lambda model: model.transcribe(str(audio), language=language, temperature=0,
                                           verbose=None, word_timestamps=True))
        if check:
            check()
        words = [{"text": w.word, "start": w.start, "end": w.end} for w in result.all_words()]
        return heard_from(words, self.name, WHISPER, _whisper_version(),
                          parameters={"device": self._whisper_device,
                                      "language": language or "detected", "temperature": "0"})

    def release(self) -> bool:
        """Let go of every model this provider has loaded (§9, slice 41, slice 82).

        **The only thing that really frees the card.** Measured, holding one provider after one
        alignment: `release_gpu_memory()` on its own gave back nothing at all, because the weights
        are still referenced and the second opinion's 3.6 GB is not torch's memory in the first
        place; dropping the models took the process from 3608 MiB to the 160 MiB of CUDA context that
        belongs to it until it exits. Both servers hold a provider between jobs now, so this is what
        the idle window calls. Loading them again costs about two seconds off a warm disk.
        """
        held = bool(self._models or self._separator or self._whisper)
        self._models, self._separator, self._whisper = {}, None, None
        self._separator_device, self._whisper_where = None, None
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
        wanted = "cpu" if self._whisper_cpu_only else (self._whisper_where or self.resolved_device())
        if self._whisper is not None and self._whisper_device != wanted:
            self._free_vram()   # held on the card and this run is on the processor, or the other way
        if self._whisper is not None:
            return self._whisper
        device = wanted
        # not "for the second opinion": the same model checks an alignment, drafts words and listens
        # for given ones, and the line above this one has already said which of the three it is
        self.log(f"loading {WHISPER}")
        try:
            self._whisper = self._load_whisper(device)
            self._whisper_device = device
        except (RuntimeError, OSError) as e:
            if device == "cuda" and _is_cuda_library_trap(e):
                # the spike hit exactly this: torch shipped CUDA 13, ctranslate2 wanted 12
                self.log(f"{CUDA_TRAP}")
                self._whisper = self._load_whisper("cpu")
                self._whisper_device = "cpu"
            else:
                raise TimingUnavailable(f"{WHISPER} could not be loaded: {e}") from e
        return self._whisper

    def _off_the_disk(self, what: str, size: str, load: Callable[[bool], Any]):
        """Load a model without asking anybody anything, and reach out only if it is not here yet.

        **A local provider that needs no network must not use one** (§9, slice 84). Tried offline
        first, always; the download on first use is unchanged and now says so in the log, once.
        `load` is given `offline` because the two libraries take it differently: the big model has its
        own `local_files_only`, and the separator goes through the hub, which `hub_offline` switches.
        """
        try:
            with hub_offline():
                return load(True)
        except Exception as e:
            # a card that will not have it, or a library mismatch, has nothing to do with the files
            if _is_cuda_library_trap(e) or _is_out_of_memory(e):
                raise
            log.debug("%s is not in the model cache", what, exc_info=True)
            self.log(f"{what} is not in the model cache yet — downloading it ({size})")
            return load(False)

    def _load_whisper(self, device: str):
        """The big model, off the disk where it can be."""
        try:
            import stable_whisper
        except ImportError as e:
            raise TimingUnavailable(NO_CHECK) from e
        kind = "float16" if device == "cuda" else "int8"
        return self._off_the_disk(WHISPER, WHISPER_SIZE, lambda offline: stable_whisper.load_faster_whisper(
            WHISPER, device=device, compute_type=kind, **({"local_files_only": True} if offline else {})))

    # -- the two models ------------------------------------------------------------------

    def _aligner(self, bundle: object, lang: str, device: str):
        if (lang, device) not in self._models:
            self.log(f"loading the {lang} aligner ({BUNDLES[lang]})")
            # torch's own cache, which asks nothing of anybody when the file is there — measured, and
            # unlike the separator (§9, slice 84)
            self._models[(lang, device)] = self._off_the_disk(
                BUNDLES[lang], "361 MB",
                lambda _offline: bundle.get_model().to(device).eval())  # type: ignore[attr-defined]
        return self._models[(lang, device)]

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
        separator = self._separator if self._separator_device == device else None
        if separator is None:
            self.log(f"loading the separator ({SEPARATOR})")
            separator = self._separator = self._off_the_disk(
                SEPARATOR, "81 MB", lambda _offline: Separator(model=SEPARATOR, device=device, progress=False))
            self._separator_device = device
        if check:
            check()
        self.log(f"separating the voice from {audio.name} …")
        _, stems = separator.separate_audio_file(audio)  # type: ignore[attr-defined]
        vocals = stems["vocals"].mean(0, keepdim=True).cpu()
        return vocals, int(separator.samplerate)  # type: ignore[attr-defined]


@contextmanager
def hub_offline():
    """Switch the model hub off for the length of one load (§9, slice 84).

    **A file that is already on the disk needs no request.** Measured on the user's own server: with
    the weights in the cache, building the separator still asked the hub for metadata and the log
    carried *"You are sending unauthenticated requests to the HF Hub"* through every job (R-284).
    The aligner does not (it is torch's own cache) and the big model takes `local_files_only`; this is
    for everything that goes through `huggingface_hub`, which reads the flag two ways — the
    environment at import time, and the module attribute at request time — so both are set.
    """
    was = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    constants = sys.modules.get("huggingface_hub.constants")
    before = getattr(constants, "HF_HUB_OFFLINE", None) if constants is not None else None
    if constants is not None:
        constants.HF_HUB_OFFLINE = True
    try:
        yield
    finally:
        if was is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = was
        # the module may only have been imported inside the block, so it is looked up again
        constants = sys.modules.get("huggingface_hub.constants")
        if constants is not None:
            constants.HF_HUB_OFFLINE = bool(before) if before is not None else False


def card_room() -> tuple[int, int]:
    """Free and total MiB on the graphics card, as the driver reports it through torch."""
    import torch

    free, total = torch.cuda.mem_get_info()
    return int(free / 2**20), int(total / 2**20)


def pool_held() -> int:
    """MiB torch has reserved in this process: already this program's, and reused rather than asked
    for a second time — which is why it counts as room for its own next job (§9, slice 82)."""
    import torch

    return int(torch.cuda.memory_reserved() / 2**20)


def separated_voice(audio: Path, into: Path, log: Callable[[str], None] | None = None,
                    check: Callable[[], None] | None = None) -> Path | None:
    """Write the track's isolated voice to `into`, or return None if that cannot be done (§9, slice 45).

    A transcriber hears far more of a song when the band is taken off it. Measured on one real
    track against its own published lyric (`docs/qa-catalog.md`, section AF): Deepgram found 16 of
    52 lines on the mix and **32** on the voice; the local decoder 28 and **38**. It is also less of
    the recording to send anywhere, which matters when the transcriber is somebody else's computer.

    Never fatal: without the `noaap[timing]` extra, or if anything goes wrong, the caller falls
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
