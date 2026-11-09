"""Shared analysis settings, requests and result types (issue #12; spec §4, §6-§9, §16).

Contract only: no algorithms live here. Every analysis is a pure function
``(Sound or source frames, AnalysisRequest) -> result`` built on these types.

Conventions fixed by this module:

- Axes reuse ``model.time.SampledAxis``. A frequency axis is a SampledAxis whose
  "times" are bin-centre frequencies in Hz.
- Value arrays are frame-major: axis 0 is the analysis frame.
- Stored values are physical units, never display-scaled. dB conversion,
  pre-emphasis, autoscaling and dynamic range are separate pure functions.
- Undefined values are NaN. Validity/voicing is explicit or derived from NaN,
  never encoded as 0 Hz or 0 dB.
- Channel handling is an explicit ``ChannelPolicy``; nothing downmixes implicitly.
- Defaults are *candidate* values from the manual and source. They stay
  candidates until captured from the pinned reference executable.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import ClassVar

import numpy as np
from numpy.typing import NDArray

from ..errors import InvalidInput
from ..model.sound import immutable_samples
from ..model.time import SampledAxis

# ---------------------------------------------------------------------------
# Units (strings are part of the contract; GUI and CLI display them verbatim)

HZ = "Hz"
SECONDS = "s"
PA2_PER_HZ = "Pa^2/Hz"
PA2 = "Pa^2"
DB_SPL = "dB re 2e-5 Pa"
RATIO = "1"
HEARING_THRESHOLD_PA = 2.0e-5


class AnalysisKind(StrEnum):
    SPECTROGRAM = "spectrogram"
    PITCH = "pitch"
    FORMANT = "formant"
    INTENSITY = "intensity"


class WindowShape(StrEnum):
    """R1 supports Gaussian only; other shapes are R2 and deliberately absent."""

    GAUSSIAN = "gaussian"


# ---------------------------------------------------------------------------
# Validation helpers


def _positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInput(f"{name} must be a number.")
    if not math.isfinite(value) or value <= 0:
        raise InvalidInput(f"{name} must be finite and positive.")


def _fraction(name: str, value: float, *, allow_zero: bool = True) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InvalidInput(f"{name} must be a finite number.")
    if value < 0 or value > 1 or (value == 0 and not allow_zero):
        raise InvalidInput(f"{name} must lie in {'[0, 1]' if allow_zero else '(0, 1]'}.")


def _non_negative(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInput(f"{name} must be a number.")
    if not math.isfinite(value) or value < 0:
        raise InvalidInput(f"{name} must be finite and non-negative.")


def _optional_step(name: str, value: float | None) -> None:
    """None means "automatic", resolved by the method; the result axis records the value."""
    if value is not None:
        _positive(name, value)


def _count(name: str, value: int) -> None:
    if type(value) is not int or value < 1:
        raise InvalidInput(f"{name} must be a positive integer.")


def _frozen(values: NDArray, dtype: type) -> NDArray:
    array = np.asarray(values, dtype=dtype)
    if dtype is np.float64:
        return immutable_samples(array)
    return np.frombuffer(array.tobytes(), dtype=dtype).reshape(array.shape)


def _shape(name: str, array: NDArray, expected: tuple[int, ...]) -> None:
    if array.shape != expected:
        raise InvalidInput(f"{name} has shape {array.shape}; expected {expected}.")


def _normalise_floats(settings: object) -> None:
    """Store float-typed fields as float so equal values hash to one cache key."""
    for field in fields(settings):
        value = getattr(settings, field.name)
        if "float" in str(field.type) and type(value) is int:
            object.__setattr__(settings, field.name, float(value))


# ---------------------------------------------------------------------------
# Requests


@dataclass(frozen=True)
class TimeRange:
    """Half-open analysis range [start, end) in source seconds."""

    start: float
    end: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise InvalidInput("Analysis range needs finite bounds.")
        if self.start >= self.end:
            raise InvalidInput("Analysis range needs start < end.")
        # Canonical floats (and +0.0 for -0.0) so equal ranges share one cache key.
        object.__setattr__(self, "start", float(self.start) + 0.0)
        object.__setattr__(self, "end", float(self.end) + 0.0)

    def contains_frames(self, axis: SampledAxis) -> bool:
        """True if every frame centre of ``axis`` lies in [start, end).

        Boundary policy matches ``SampledAxis.frame_range``: a centre within 1e-9
        frame steps of a bound counts as on it, so it is in at ``start`` and out at
        ``end``. The axis's declared domain may extend beyond the range (e.g.
        analysis-window padding); only returned frame centres are constrained.
        """
        tolerance = 1e-9 * axis.dx
        first = axis.x1
        last = axis.x1 + (axis.nx - 1) * axis.dx
        return first >= self.start - tolerance and last < self.end - tolerance


class ChannelMode(StrEnum):
    SINGLE = "single"
    """Analyse one zero-based channel."""
    COMBINED = "combined"
    """Use the method's documented multichannel rule (e.g. pooled power).

    Each analysis states its rule in its docstring and rejects COMBINED if it has none.
    """


@dataclass(frozen=True)
class ChannelPolicy:
    mode: ChannelMode
    channel: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ChannelMode):
            raise InvalidInput("Channel mode must be a ChannelMode.")
        if self.mode is ChannelMode.SINGLE:
            if type(self.channel) is not int or self.channel < 0:
                raise InvalidInput("A single-channel policy needs a zero-based channel index.")
        elif self.channel is not None:
            raise InvalidInput("A combined-channel policy takes no channel index.")

    @classmethod
    def single(cls, channel: int) -> ChannelPolicy:
        return cls(ChannelMode.SINGLE, channel)

    @classmethod
    def combined(cls) -> ChannelPolicy:
        return cls(ChannelMode.COMBINED)

    def check(self, channels: int) -> None:
        if self.mode is ChannelMode.SINGLE and not self.channel < channels:
            raise InvalidInput("Channel index is outside this recording.")


# ---------------------------------------------------------------------------
# Settings (frozen, validated; defaults are reference candidates)


@dataclass(frozen=True)
class SpectrogramSettings:
    """``Sound: To Spectrogram...`` form defaults (spec §6)."""

    kind: ClassVar[AnalysisKind] = AnalysisKind.SPECTROGRAM
    window_length: float = 0.005
    """Effective length in s; the Gaussian's physical span is twice this."""
    maximum_frequency: float = 5000.0
    time_step: float = 0.002
    """Requested; the method clamps to (L/sqrt(pi))/8 and the axis records the result."""
    frequency_step: float = 20.0
    """Requested; clamped to 8x oversampling and snapped to the FFT bin width."""
    window_shape: WindowShape = WindowShape.GAUSSIAN

    def __post_init__(self) -> None:
        for name in ("window_length", "maximum_frequency", "time_step", "frequency_step"):
            _positive(name, getattr(self, name))
        if not isinstance(self.window_shape, WindowShape):
            raise InvalidInput("Unsupported window shape.")
        _normalise_floats(self)


@dataclass(frozen=True)
class PitchSettings:
    """``Sound: To Pitch (filtered autocorrelation)...`` defaults (spec §7)."""

    kind: ClassVar[AnalysisKind] = AnalysisKind.PITCH
    time_step: float | None = None
    pitch_floor: float = 50.0
    pitch_top: float = 800.0
    max_candidates: int = 15
    very_accurate: bool = False
    attenuation_at_top: float = 0.03
    silence_threshold: float = 0.09
    voicing_threshold: float = 0.50
    octave_cost: float = 0.055
    octave_jump_cost: float = 0.35
    voiced_unvoiced_cost: float = 0.14

    def __post_init__(self) -> None:
        _optional_step("time_step", self.time_step)
        _positive("pitch_floor", self.pitch_floor)
        _positive("pitch_top", self.pitch_top)
        if self.pitch_top <= self.pitch_floor:
            raise InvalidInput("Pitch top must exceed the pitch floor.")
        _count("max_candidates", self.max_candidates)
        if type(self.very_accurate) is not bool:
            raise InvalidInput("very_accurate must be a boolean.")
        _fraction("attenuation_at_top", self.attenuation_at_top, allow_zero=False)
        if self.attenuation_at_top == 1:
            raise InvalidInput("attenuation_at_top must be below 1.")
        _fraction("silence_threshold", self.silence_threshold)
        _fraction("voicing_threshold", self.voicing_threshold)
        for name in ("octave_cost", "octave_jump_cost", "voiced_unvoiced_cost"):
            _non_negative(name, getattr(self, name))
        _normalise_floats(self)


@dataclass(frozen=True)
class FormantSettings:
    """``Sound: To Formant (burg)...`` defaults (spec §8)."""

    kind: ClassVar[AnalysisKind] = AnalysisKind.FORMANT
    time_step: float | None = None
    max_formants: float = 5.0
    """A multiple of 0.5; the method uses round(2 * max_formants) poles."""
    formant_ceiling: float = 5500.0
    window_length: float = 0.025
    preemphasis_from: float = 50.0

    def __post_init__(self) -> None:
        _optional_step("time_step", self.time_step)
        _positive("max_formants", self.max_formants)
        if (2 * self.max_formants) != int(2 * self.max_formants):
            raise InvalidInput("max_formants must be a multiple of 0.5.")
        _positive("formant_ceiling", self.formant_ceiling)
        _positive("window_length", self.window_length)
        _non_negative("preemphasis_from", self.preemphasis_from)
        _normalise_floats(self)


@dataclass(frozen=True)
class IntensitySettings:
    """``Sound: To Intensity...`` defaults (spec §9)."""

    kind: ClassVar[AnalysisKind] = AnalysisKind.INTENSITY
    pitch_floor: float = 100.0
    time_step: float | None = None
    subtract_mean: bool = True

    def __post_init__(self) -> None:
        _positive("pitch_floor", self.pitch_floor)
        _optional_step("time_step", self.time_step)
        if type(self.subtract_mean) is not bool:
            raise InvalidInput("subtract_mean must be a boolean.")
        _normalise_floats(self)


Settings = SpectrogramSettings | PitchSettings | FormantSettings | IntensitySettings


def _settings_record(settings: Settings) -> dict:
    record = {"kind": settings.kind.value}
    for field in fields(settings):
        value = getattr(settings, field.name)
        record[field.name] = value.value if isinstance(value, StrEnum) else value
    return record


@dataclass(frozen=True)
class AnalysisRequest:
    """What to analyse. Results echo it so callers can drop stale or reordered work (§16)."""

    source_id: str
    revision: int
    settings: Settings
    time_range: TimeRange
    channels: ChannelPolicy
    token: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise InvalidInput("An analysis request needs a source ID.")
        if type(self.revision) is not int or self.revision < 0:
            raise InvalidInput("Source revision must be a non-negative integer.")
        if not isinstance(self.settings, Settings):
            raise InvalidInput("Unknown analysis settings type.")
        if not isinstance(self.time_range, TimeRange):
            raise InvalidInput("An analysis request needs a TimeRange.")
        if not isinstance(self.channels, ChannelPolicy):
            raise InvalidInput("An analysis request needs a ChannelPolicy.")
        if type(self.token) is not int:
            raise InvalidInput("Request token must be an integer.")

    @property
    def kind(self) -> AnalysisKind:
        return self.settings.kind

    def is_current(self, source_id: str, revision: int, token: int) -> bool:
        return (self.source_id, self.revision, self.token) == (source_id, revision, token)


@dataclass(frozen=True)
class Algorithm:
    id: str
    """Stable identifier, e.g. "spectrogram.gaussian-stft"."""
    version: str
    """Bump whenever numerical output can change; it is part of the cache key."""

    def __post_init__(self) -> None:
        if not self.id or not self.version:
            raise InvalidInput("Algorithm needs an id and a version.")


@dataclass(frozen=True)
class Provenance:
    request: AnalysisRequest
    algorithm: Algorithm

    def record(self) -> dict:
        """JSON-ready description for CSV/JSON export (spec R1 export row)."""
        request = self.request
        return {
            "algorithm": asdict(self.algorithm),
            "source_id": request.source_id,
            "revision": request.revision,
            "settings": _settings_record(request.settings),
            "time_range": [request.time_range.start, request.time_range.end],
            "channels": {"mode": request.channels.mode.value, "channel": request.channels.channel},
        }

    @property
    def cache_key(self) -> str:
        """Identity, revision, channel policy, range, every setting and algorithm version.

        The request token is excluded: two identical requests share a cached result.
        """
        text = json.dumps(self.record(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_provenance(provenance: Provenance, kind: AnalysisKind, time: SampledAxis) -> None:
    """Shared by all result types: right kind, and frames inside the requested range."""
    if not isinstance(provenance, Provenance) or provenance.request.kind is not kind:
        raise InvalidInput(f"Result provenance does not describe a {kind.value} analysis.")
    if not isinstance(time, SampledAxis):
        raise InvalidInput("A result's time axis must be a SampledAxis.")
    if not provenance.request.time_range.contains_frames(time):
        raise InvalidInput("Result frame times lie outside the requested time range.")


# ---------------------------------------------------------------------------
# Results (immutable; frame-major; NaN = undefined)


@dataclass(frozen=True, eq=False)
class Spectrogram:
    """Power spectral density, shape (frames, bins), in Pa^2/Hz (linear, not dB).

    ``valid`` marks computed cells; invalid cells (e.g. above Nyquist) hold NaN.
    """

    provenance: Provenance
    time: SampledAxis
    frequency: SampledAxis
    power: NDArray[np.float64]
    valid: NDArray[np.bool_]
    units: ClassVar[dict[str, str]] = {"time": SECONDS, "frequency": HZ, "power": PA2_PER_HZ}

    def __post_init__(self) -> None:
        _check_provenance(self.provenance, AnalysisKind.SPECTROGRAM, self.time)
        expected = (self.time.nx, self.frequency.nx)
        power, valid = np.asarray(self.power, dtype=np.float64), np.asarray(self.valid, dtype=bool)
        _shape("power", power, expected)
        _shape("valid", valid, expected)
        if not np.isnan(power[~valid]).all():
            raise InvalidInput("Invalid spectrogram cells must hold NaN.")
        cells = power[valid]
        if not (np.isfinite(cells) & (cells >= 0)).all():
            raise InvalidInput("Valid spectrogram power must be finite and non-negative.")
        object.__setattr__(self, "power", _frozen(power, np.float64))
        object.__setattr__(self, "valid", _frozen(valid, np.bool_))


@dataclass(frozen=True, eq=False)
class Pitch:
    """Candidates per frame plus the chosen path (spec §4, §7).

    Slot layout follows Praat's frame: ``candidate_count[i]`` slots are in use.
    The unvoiced candidate has NaN frequency and a finite strength. Unused slots
    are NaN in both arrays. ``chosen[i]`` indexes the path's candidate. A frame is
    voiced when its chosen frequency f satisfies 0 < f < ceiling (Praat's rule),
    so an above-ceiling choice reads as unvoiced rather than as a value.
    """

    provenance: Provenance
    time: SampledAxis
    ceiling: float
    intensity: NDArray[np.float64]
    """Relative frame intensity in [0, 1] (local peak / global peak)."""
    candidate_frequency: NDArray[np.float64]
    candidate_strength: NDArray[np.float64]
    candidate_count: NDArray[np.int64]
    chosen: NDArray[np.int64]
    units: ClassVar[dict[str, str]] = {
        "time": SECONDS,
        "frequency": HZ,
        "strength": RATIO,
        "intensity": RATIO,
    }

    def __post_init__(self) -> None:
        _check_provenance(self.provenance, AnalysisKind.PITCH, self.time)
        _positive("ceiling", self.ceiling)
        nx = self.time.nx
        frequency = np.asarray(self.candidate_frequency, dtype=np.float64)
        strength = np.asarray(self.candidate_strength, dtype=np.float64)
        if frequency.ndim != 2 or frequency.shape[0] != nx or frequency.shape[1] < 1:
            raise InvalidInput("candidate_frequency must have shape (frames, slots).")
        _shape("candidate_strength", strength, frequency.shape)
        intensity = np.asarray(self.intensity, dtype=np.float64)
        _shape("intensity", intensity, (nx,))
        if not (np.isfinite(intensity) & (intensity >= 0) & (intensity <= 1)).all():
            raise InvalidInput("Relative intensity must lie in [0, 1].")
        count = np.asarray(self.candidate_count)
        chosen = np.asarray(self.chosen)
        _shape("candidate_count", count, (nx,))
        _shape("chosen", chosen, (nx,))
        if not all(np.issubdtype(a.dtype, np.integer) for a in (count, chosen)):
            raise InvalidInput("Candidate counts and choices must be integers.")
        slots = frequency.shape[1]
        if ((count < 1) | (count > slots)).any() or ((chosen < 0) | (chosen >= count)).any():
            raise InvalidInput("Each frame needs 1..slots candidates and a chosen index in range.")
        used = np.arange(slots)[None, :] < count[:, None]
        if not (np.isnan(frequency[~used]).all() and np.isnan(strength[~used]).all()):
            raise InvalidInput("Unused candidate slots must be NaN.")
        if not np.isfinite(strength[used]).all():
            raise InvalidInput("Every used candidate needs a finite strength.")
        live = frequency[used]
        if not ((np.isnan(live)) | (np.isfinite(live) & (live > 0))).all():
            raise InvalidInput("Candidate frequencies are positive Hz or NaN for unvoiced.")
        object.__setattr__(self, "intensity", _frozen(intensity, np.float64))
        object.__setattr__(self, "candidate_frequency", _frozen(frequency, np.float64))
        object.__setattr__(self, "candidate_strength", _frozen(strength, np.float64))
        object.__setattr__(self, "candidate_count", _frozen(count, np.int64))
        object.__setattr__(self, "chosen", _frozen(chosen, np.int64))

    @property
    def frequency(self) -> NDArray[np.float64]:
        """Chosen F0 per frame in Hz; NaN where unvoiced."""
        rows = np.arange(self.time.nx)
        f = self.candidate_frequency[rows, self.chosen]
        return np.where(self.voiced, f, np.nan)

    @property
    def strength(self) -> NDArray[np.float64]:
        return self.candidate_strength[np.arange(self.time.nx), self.chosen]

    @property
    def voiced(self) -> NDArray[np.bool_]:
        f = self.candidate_frequency[np.arange(self.time.nx), self.chosen]
        with np.errstate(invalid="ignore"):
            return np.isfinite(f) & (f > 0) & (f < self.ceiling)


@dataclass(frozen=True, eq=False)
class Formant:
    """Burg formants, frame-major; ``count[i]`` leading slots are defined (spec §8).

    Frequencies are sorted ascending within a frame. Unused slots are NaN.
    A frame with count 0 is valid (e.g. all-zero input) and has no formants.
    """

    provenance: Provenance
    time: SampledAxis
    intensity: NDArray[np.float64]
    """Per-frame peak squared sample of the pre-emphasised window, in Pa^2."""
    count: NDArray[np.int64]
    frequency: NDArray[np.float64]
    bandwidth: NDArray[np.float64]
    units: ClassVar[dict[str, str]] = {
        "time": SECONDS,
        "frequency": HZ,
        "bandwidth": HZ,
        "intensity": PA2,
    }

    def __post_init__(self) -> None:
        _check_provenance(self.provenance, AnalysisKind.FORMANT, self.time)
        nx = self.time.nx
        frequency = np.asarray(self.frequency, dtype=np.float64)
        bandwidth = np.asarray(self.bandwidth, dtype=np.float64)
        if frequency.ndim != 2 or frequency.shape[0] != nx:
            raise InvalidInput("frequency must have shape (frames, slots).")
        _shape("bandwidth", bandwidth, frequency.shape)
        intensity = np.asarray(self.intensity, dtype=np.float64)
        _shape("intensity", intensity, (nx,))
        if not (np.isfinite(intensity) & (intensity >= 0)).all():
            raise InvalidInput("Formant frame intensity must be finite and non-negative.")
        count = np.asarray(self.count)
        _shape("count", count, (nx,))
        if not np.issubdtype(count.dtype, np.integer):
            raise InvalidInput("Formant counts must be integers.")
        slots = frequency.shape[1]
        if ((count < 0) | (count > slots)).any():
            raise InvalidInput("Formant counts must lie in 0..slots.")
        used = np.arange(slots)[None, :] < count[:, None]
        if not (np.isnan(frequency[~used]).all() and np.isnan(bandwidth[~used]).all()):
            raise InvalidInput("Unused formant slots must be NaN.")
        live_f, live_b = frequency[used], bandwidth[used]
        if not (np.isfinite(live_f) & (live_f > 0)).all():
            raise InvalidInput("Defined formant frequencies must be positive and finite.")
        if not (np.isfinite(live_b) & (live_b >= 0)).all():
            raise InvalidInput("Defined bandwidths must be finite and non-negative.")
        with np.errstate(invalid="ignore"):
            descending = np.diff(frequency, axis=1) < 0
        if (descending & used[:, 1:]).any():
            raise InvalidInput("Formants must be sorted by frequency within a frame.")
        object.__setattr__(self, "intensity", _frozen(intensity, np.float64))
        object.__setattr__(self, "count", _frozen(count, np.int64))
        object.__setattr__(self, "frequency", _frozen(frequency, np.float64))
        object.__setattr__(self, "bandwidth", _frozen(bandwidth, np.float64))


@dataclass(frozen=True, eq=False)
class Intensity:
    """Intensity contour in dB re 2e-5 Pa; NaN marks undefined frames (spec §9)."""

    provenance: Provenance
    time: SampledAxis
    values: NDArray[np.float64]
    reference_pa: ClassVar[float] = HEARING_THRESHOLD_PA
    units: ClassVar[dict[str, str]] = {"time": SECONDS, "values": DB_SPL}

    def __post_init__(self) -> None:
        _check_provenance(self.provenance, AnalysisKind.INTENSITY, self.time)
        values = np.asarray(self.values, dtype=np.float64)
        _shape("values", values, (self.time.nx,))
        if np.isinf(values).any():
            raise InvalidInput("Intensity values are finite dB or NaN.")
        object.__setattr__(self, "values", _frozen(values, np.float64))

    @property
    def valid(self) -> NDArray[np.bool_]:
        return np.isfinite(self.values)


AnalysisResult = Spectrogram | Pitch | Formant | Intensity


def accept(result: AnalysisResult, source_id: str, revision: int, token: int) -> bool:
    """True if ``result`` answers the caller's current request; stale results are dropped."""
    return result.provenance.request.is_current(source_id, revision, token)
