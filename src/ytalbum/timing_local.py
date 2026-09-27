"""The local timing provider: separate the voice, then force-align the words to it (§9.36).

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
import unicodedata
from collections.abc import Callable
from pathlib import Path

from .timing import ALIGN, Timed, TimedLine, TimingUnavailable, language_of

log = logging.getLogger(__name__)

# torchaudio's own pipelines, which is all the spike's best arm ever used
BUNDLES = {"en": "WAV2VEC2_ASR_BASE_960H", "de": "VOXPOPULI_ASR_BASE_10K_DE"}
SEPARATOR = "htdemucs"
BLANK = 0  # index 0 is the CTC blank in both bundles (their label 0 is "-"), never a letter
MISSING = ("the local timing provider needs the optional extra: "
           'uv pip install "ytalbum[timing]" (torch, torchaudio, demucs)')


class LocalTiming:
    """Alignment on this machine. Nothing is sent anywhere; nothing is imported until asked."""

    name = "local"

    def __init__(self, device: str = "auto", log: Callable[[str], None] | None = None) -> None:
        self.device = device
        self.log = log or (lambda _: None)
        self._models: dict[str, object] = {}
        self._separator: object | None = None

    # -- what it can do ----------------------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        """Align only. Deriving words is a different job and a different model (§9.36)."""
        try:
            import torch  # noqa: F401
            import torchaudio  # noqa: F401
        except ImportError:
            return frozenset()
        return frozenset({ALIGN})

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
        for w, token in enumerate(first_token):
            if token >= len(spans):
                break
            starts.setdefault(owners[w], round(spans[token].start * seconds, 2))
            last = min(token + 1, len(spans) - 1)
            ends[owners[w]] = round(spans[last].end * seconds, 2)
        placed = [TimedLine(text=line, start=starts.get(i), end=ends.get(i)) for i, line in enumerate(lines)]
        return Timed(lines=placed, provider=self.name, model=f"{BUNDLES[lang]} + {SEPARATOR}",
                     version=torchaudio.__version__,
                     parameters={"device": device, "language": lang, "separated": "vocals"})

    def transcribe(self, audio: Path, *, language: str | None = None,
                   check: Callable[[], None] | None = None) -> Timed:
        raise TimingUnavailable(
            "this provider only places words it is given. Deriving them needs a much larger model, "
            "which the spike found is the weaker half of the job (docs/spikes/2026-09-alignment.md)")

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

        if self._separator is None:
            self.log(f"loading the separator ({SEPARATOR}, 81 MB on first use)")
            self._separator = Separator(model=SEPARATOR, device=device, progress=False)
        if check:
            check()
        self.log(f"separating the voice from {audio.name} …")
        _, stems = self._separator.separate_audio_file(audio)  # type: ignore[attr-defined]
        vocals = stems["vocals"].mean(0, keepdim=True).cpu()
        return vocals, int(self._separator.samplerate)  # type: ignore[attr-defined]


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
