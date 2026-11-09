import json
import math
from dataclasses import fields, replace

import numpy as np
import pytest

from langtools_praat.analysis.contracts import (
    Algorithm,
    AnalysisKind,
    AnalysisRequest,
    ChannelMode,
    ChannelPolicy,
    Formant,
    FormantSettings,
    Intensity,
    IntensitySettings,
    Pitch,
    PitchSettings,
    Provenance,
    Spectrogram,
    SpectrogramSettings,
    TimeRange,
    accept,
)
from langtools_praat.errors import InvalidInput
from langtools_praat.model.time import SampledAxis

NAN = math.nan
ALGO = Algorithm("test.algorithm", "1")
POSITIVE_BAD = [0, -1.0, NAN, math.inf, -math.inf, True, "1"]
NON_NEGATIVE_BAD = [-1.0, NAN, math.inf, True, "1"]
FRACTION_BAD = [-0.1, 1.5, NAN, math.inf, True]
DEFAULTS = [SpectrogramSettings(), PitchSettings(), FormantSettings(), IntensitySettings()]

TIME3 = SampledAxis(0.0, 0.3, 3, 0.1, 0.05)
SPEC_FREQ = SampledAxis(0.0, 1000.0, 3, 250.0, 0.0)
PITCH_TIME = SampledAxis(0.0, 1.0, 4, 0.25, 0.125)


# ---------------------------------------------------------------------------
# Builders


def _request(settings=None, **overrides):
    values = {
        "source_id": "rec",
        "revision": 1,
        "settings": SpectrogramSettings() if settings is None else settings,
        "time_range": TimeRange(0.0, 0.3),
        "channels": ChannelPolicy.single(0),
        "token": 0,
    }
    values.update(overrides)
    return AnalysisRequest(**values)


def _provenance(settings=None, algorithm=ALGO, **overrides):
    return Provenance(_request(settings, **overrides), algorithm)


def _spectrogram_kwargs():
    valid = np.ones((3, 3), dtype=bool)
    valid[:, 2] = False
    power = np.array([[1.0, 2.0, NAN], [0.5, 0.25, NAN], [0.0, 3.0, NAN]])
    return {
        "provenance": _provenance(SpectrogramSettings()),
        "time": TIME3,
        "frequency": SPEC_FREQ,
        "power": power,
        "valid": valid,
    }


def _pitch_kwargs():
    # Frame 0: chosen slot 0 = 200 Hz, voiced.
    # Frame 1: chosen slot 0 is the unvoiced candidate (NaN); slot 1 = 300 Hz is not on the path.
    # Frame 2: chosen 5000 Hz sits exactly at the ceiling, so it reads as unvoiced.
    # Frame 3: three candidates, chosen slot 2 = 220 Hz.
    return {
        "provenance": _provenance(PitchSettings(), time_range=TimeRange(0.0, 1.0)),
        "time": PITCH_TIME,
        "ceiling": 5000.0,
        "intensity": np.array([0.2, 0.5, 1.0, 0.8]),
        "candidate_frequency": np.array(
            [
                [200.0, NAN, NAN],
                [NAN, 300.0, NAN],
                [5000.0, NAN, NAN],
                [150.0, 180.0, 220.0],
            ]
        ),
        "candidate_strength": np.array(
            [
                [0.9, 0.4, NAN],
                [0.5, 0.2, NAN],
                [0.7, NAN, NAN],
                [0.1, 0.2, 0.3],
            ]
        ),
        "candidate_count": np.array([2, 2, 1, 3], dtype=np.int64),
        "chosen": np.array([0, 0, 0, 2], dtype=np.int64),
    }


def _formant_kwargs():
    return {
        "provenance": _provenance(FormantSettings()),
        "time": TIME3,
        "intensity": np.array([1e-4, 0.0, 0.5]),
        "count": np.array([2, 0, 3], dtype=np.int64),
        "frequency": np.array(
            [
                [500.0, 1500.0, NAN],
                [NAN, NAN, NAN],
                [300.0, 2300.0, 3000.0],
            ]
        ),
        "bandwidth": np.array(
            [
                [60.0, 90.0, NAN],
                [NAN, NAN, NAN],
                [50.0, 100.0, 200.0],
            ]
        ),
    }


def _intensity(values=None, provenance=None):
    if values is None:
        values = np.array([60.0, NAN, 80.0])
    if provenance is None:
        provenance = _provenance(IntensitySettings())
    return Intensity(provenance=provenance, time=TIME3, values=values)


# ---------------------------------------------------------------------------
# Settings


def test_spectrogram_settings_defaults():
    s = SpectrogramSettings()
    assert s.kind is AnalysisKind.SPECTROGRAM
    assert (s.window_length, s.maximum_frequency) == (0.005, 5000.0)
    assert (s.time_step, s.frequency_step) == (0.002, 20.0)


def test_pitch_settings_defaults():
    s = PitchSettings()
    assert s.kind is AnalysisKind.PITCH
    assert s.time_step is None
    assert (s.pitch_floor, s.pitch_top, s.max_candidates) == (50.0, 800.0, 15)
    assert s.very_accurate is False
    assert s.attenuation_at_top == 0.03
    assert (s.silence_threshold, s.voicing_threshold) == (0.09, 0.50)
    assert (s.octave_cost, s.octave_jump_cost, s.voiced_unvoiced_cost) == (0.055, 0.35, 0.14)


def test_formant_settings_defaults():
    s = FormantSettings()
    assert s.kind is AnalysisKind.FORMANT
    assert s.time_step is None
    assert (s.max_formants, s.formant_ceiling) == (5.0, 5500.0)
    assert (s.window_length, s.preemphasis_from) == (0.025, 50.0)


def test_intensity_settings_defaults():
    s = IntensitySettings()
    assert s.kind is AnalysisKind.INTENSITY
    assert (s.pitch_floor, s.time_step, s.subtract_mean) == (100.0, None, True)


@pytest.mark.parametrize("field", ["window_length", "maximum_frequency"])
@pytest.mark.parametrize("bad", POSITIVE_BAD)
def test_spectrogram_rejects_bad_lengths_and_frequency(field, bad):
    with pytest.raises(InvalidInput):
        SpectrogramSettings(**{field: bad})


@pytest.mark.parametrize("field", ["time_step", "frequency_step"])
@pytest.mark.parametrize("bad", POSITIVE_BAD)
def test_spectrogram_rejects_bad_steps(field, bad):
    with pytest.raises(InvalidInput):
        SpectrogramSettings(**{field: bad})


def test_spectrogram_rejects_unknown_window_shape():
    with pytest.raises(InvalidInput):
        SpectrogramSettings(window_shape="hamming")


@pytest.mark.parametrize("field", ["pitch_floor", "pitch_top"])
@pytest.mark.parametrize("bad", POSITIVE_BAD)
def test_pitch_rejects_bad_floor_and_top(field, bad):
    with pytest.raises(InvalidInput):
        PitchSettings(**{field: bad})


@pytest.mark.parametrize("floor, top", [(100.0, 100.0), (100.0, 50.0)])
def test_pitch_top_must_exceed_floor(floor, top):
    with pytest.raises(InvalidInput):
        PitchSettings(pitch_floor=floor, pitch_top=top)


@pytest.mark.parametrize("bad", [0, 1.5, True, -1, "15", 15.0])
def test_pitch_rejects_bad_max_candidates(bad):
    with pytest.raises(InvalidInput):
        PitchSettings(max_candidates=bad)


def test_pitch_accepts_single_candidate():
    assert PitchSettings(max_candidates=1).max_candidates == 1


@pytest.mark.parametrize("bad", [1, 0, "yes", None])
def test_pitch_very_accurate_must_be_bool(bad):
    with pytest.raises(InvalidInput):
        PitchSettings(very_accurate=bad)


@pytest.mark.parametrize("bad", [0, 1, 1.5, -0.1, NAN, math.inf, True])
def test_pitch_attenuation_at_top_rejects_zero_one_and_out_of_range(bad):
    with pytest.raises(InvalidInput):
        PitchSettings(attenuation_at_top=bad)


@pytest.mark.parametrize("field", ["silence_threshold", "voicing_threshold"])
@pytest.mark.parametrize("bad", FRACTION_BAD)
def test_pitch_thresholds_must_lie_in_unit_interval(field, bad):
    with pytest.raises(InvalidInput):
        PitchSettings(**{field: bad})


@pytest.mark.parametrize("field", ["silence_threshold", "voicing_threshold"])
@pytest.mark.parametrize("ok", [0.0, 1.0])
def test_pitch_thresholds_accept_unit_endpoints(field, ok):
    assert getattr(PitchSettings(**{field: ok}), field) == ok


@pytest.mark.parametrize("field", ["octave_cost", "octave_jump_cost", "voiced_unvoiced_cost"])
@pytest.mark.parametrize("bad", NON_NEGATIVE_BAD)
def test_pitch_costs_must_be_non_negative(field, bad):
    with pytest.raises(InvalidInput):
        PitchSettings(**{field: bad})


@pytest.mark.parametrize("bad", [0, -1, 5.25, 0.25, NAN, math.inf, True])
def test_formant_rejects_bad_max_formants(bad):
    with pytest.raises(InvalidInput):
        FormantSettings(max_formants=bad)


@pytest.mark.parametrize("ok", [0.5, 1.0, 4.5, 5.0])
def test_formant_accepts_half_integer_max_formants(ok):
    assert FormantSettings(max_formants=ok).max_formants == ok


@pytest.mark.parametrize("field", ["formant_ceiling", "window_length"])
@pytest.mark.parametrize("bad", POSITIVE_BAD)
def test_formant_rejects_bad_positive_fields(field, bad):
    with pytest.raises(InvalidInput):
        FormantSettings(**{field: bad})


@pytest.mark.parametrize("bad", NON_NEGATIVE_BAD)
def test_formant_preemphasis_must_be_non_negative(bad):
    with pytest.raises(InvalidInput):
        FormantSettings(preemphasis_from=bad)


def test_formant_preemphasis_accepts_zero():
    assert FormantSettings(preemphasis_from=0.0).preemphasis_from == 0.0


@pytest.mark.parametrize("bad", POSITIVE_BAD)
def test_intensity_rejects_bad_pitch_floor(bad):
    with pytest.raises(InvalidInput):
        IntensitySettings(pitch_floor=bad)


@pytest.mark.parametrize("bad", [1, 0, "yes", None])
def test_intensity_subtract_mean_must_be_bool(bad):
    with pytest.raises(InvalidInput):
        IntensitySettings(subtract_mean=bad)


@pytest.mark.parametrize(
    "settings",
    [PitchSettings, FormantSettings, IntensitySettings],
)
def test_time_step_none_is_automatic(settings):
    assert settings(time_step=None).time_step is None


@pytest.mark.parametrize("settings", [PitchSettings, FormantSettings, IntensitySettings])
@pytest.mark.parametrize("bad", POSITIVE_BAD)
def test_time_step_rejects_bad_values_when_given(settings, bad):
    with pytest.raises(InvalidInput):
        settings(time_step=bad)


# ---------------------------------------------------------------------------
# Time range and channels


def test_time_range_accepts_ordered_finite_bounds():
    r = TimeRange(0.0, 1.0)
    assert (r.start, r.end) == (0.0, 1.0)


@pytest.mark.parametrize("start, end", [(1.0, 1.0), (2.0, 1.0)])
def test_time_range_requires_start_before_end(start, end):
    with pytest.raises(InvalidInput):
        TimeRange(start, end)


@pytest.mark.parametrize(
    "start, end",
    [(NAN, 1.0), (0.0, NAN), (-math.inf, 1.0), (0.0, math.inf)],
)
def test_time_range_rejects_non_finite_bounds(start, end):
    with pytest.raises(InvalidInput):
        TimeRange(start, end)


def test_single_channel_policy():
    policy = ChannelPolicy.single(3)
    assert policy.mode is ChannelMode.SINGLE
    assert policy.channel == 3


@pytest.mark.parametrize("bad", [-1, True, 1.0, None])
def test_single_channel_rejects_bad_index(bad):
    with pytest.raises(InvalidInput):
        ChannelPolicy.single(bad)


def test_combined_policy_takes_no_index():
    policy = ChannelPolicy.combined()
    assert policy.mode is ChannelMode.COMBINED
    assert policy.channel is None
    with pytest.raises(InvalidInput):
        ChannelPolicy(ChannelMode.COMBINED, 0)


def test_channel_check_rejects_index_outside_recording():
    ChannelPolicy.single(1).check(2)
    with pytest.raises(InvalidInput):
        ChannelPolicy.single(2).check(2)
    with pytest.raises(InvalidInput):
        ChannelPolicy.single(0).check(0)


def test_combined_policy_passes_channel_check():
    ChannelPolicy.combined().check(1)


# ---------------------------------------------------------------------------
# Requests


@pytest.mark.parametrize(
    "settings, kind",
    [
        (SpectrogramSettings(), AnalysisKind.SPECTROGRAM),
        (PitchSettings(), AnalysisKind.PITCH),
        (FormantSettings(), AnalysisKind.FORMANT),
        (IntensitySettings(), AnalysisKind.INTENSITY),
    ],
)
def test_request_kind_follows_settings(settings, kind):
    assert _request(settings).kind is kind


@pytest.mark.parametrize("source_id", ["", None, 3])
def test_request_needs_nonempty_string_source(source_id):
    with pytest.raises(InvalidInput):
        _request(source_id=source_id)


@pytest.mark.parametrize("revision", [-1, 1.5, True])
def test_request_revision_must_be_non_negative_int(revision):
    with pytest.raises(InvalidInput):
        _request(revision=revision)


@pytest.mark.parametrize("token", [1.0, True, "0"])
def test_request_token_must_be_int(token):
    with pytest.raises(InvalidInput):
        _request(token=token)


@pytest.mark.parametrize("settings", [None, {"kind": "pitch"}, TimeRange(0.0, 1.0)])
def test_request_rejects_unknown_settings_type(settings):
    with pytest.raises(InvalidInput):
        AnalysisRequest(
            source_id="rec",
            revision=0,
            settings=settings,
            time_range=TimeRange(0.0, 1.0),
            channels=ChannelPolicy.single(0),
        )


# ---------------------------------------------------------------------------
# Provenance identity


SETTING_CHANGES = [
    (SpectrogramSettings(), "window_length", 0.006),
    (SpectrogramSettings(), "maximum_frequency", 5500.0),
    (SpectrogramSettings(), "time_step", 0.003),
    (SpectrogramSettings(), "frequency_step", 10.0),
    (PitchSettings(), "time_step", 0.01),
    (PitchSettings(), "pitch_floor", 60.0),
    (PitchSettings(), "pitch_top", 700.0),
    (PitchSettings(), "max_candidates", 14),
    (PitchSettings(), "very_accurate", True),
    (PitchSettings(), "attenuation_at_top", 0.04),
    (PitchSettings(), "silence_threshold", 0.1),
    (PitchSettings(), "voicing_threshold", 0.6),
    (PitchSettings(), "octave_cost", 0.06),
    (PitchSettings(), "octave_jump_cost", 0.4),
    (PitchSettings(), "voiced_unvoiced_cost", 0.15),
    (FormantSettings(), "time_step", 0.01),
    (FormantSettings(), "max_formants", 4.5),
    (FormantSettings(), "formant_ceiling", 5000.0),
    (FormantSettings(), "window_length", 0.03),
    (FormantSettings(), "preemphasis_from", 60.0),
    (IntensitySettings(), "pitch_floor", 90.0),
    (IntensitySettings(), "time_step", 0.01),
    (IntensitySettings(), "subtract_mean", False),
]


def test_setting_changes_cover_every_field():
    # window_shape has one member in R1, so it cannot be varied.
    for base in DEFAULTS:
        covered = {f for b, f, _ in SETTING_CHANGES if type(b) is type(base)}
        expected = {f.name for f in fields(base)} - {"window_shape"}
        assert covered == expected


def test_cache_key_is_a_sha256_hex_digest():
    key = _provenance().cache_key
    assert len(key) == 64
    int(key, 16)


def test_cache_key_ignores_token():
    assert _provenance(token=0).cache_key == _provenance(token=7).cache_key


def test_cache_key_is_stable_for_equal_requests():
    assert _provenance(PitchSettings()).cache_key == _provenance(PitchSettings()).cache_key


@pytest.mark.parametrize("base, field, value", SETTING_CHANGES)
def test_cache_key_changes_with_each_setting(base, field, value):
    changed = replace(base, **{field: value})
    assert _provenance(base).cache_key != _provenance(changed).cache_key


def test_cache_key_changes_with_revision():
    assert _provenance(revision=1).cache_key != _provenance(revision=2).cache_key


def test_cache_key_changes_with_source_id():
    assert _provenance(source_id="a").cache_key != _provenance(source_id="b").cache_key


def test_cache_key_changes_with_channel_policy():
    single0 = _provenance(channels=ChannelPolicy.single(0)).cache_key
    assert single0 != _provenance(channels=ChannelPolicy.single(1)).cache_key
    assert single0 != _provenance(channels=ChannelPolicy.combined()).cache_key


def test_cache_key_changes_with_time_range():
    assert _provenance().cache_key != _provenance(time_range=TimeRange(0.0, 0.4)).cache_key
    assert _provenance().cache_key != _provenance(time_range=TimeRange(0.1, 0.3)).cache_key


def test_cache_key_changes_with_algorithm_version():
    other = Algorithm("test.algorithm", "2")
    assert _provenance().cache_key != _provenance(algorithm=other).cache_key


def test_cache_key_changes_with_algorithm_id():
    other = Algorithm("test.other", "1")
    assert _provenance().cache_key != _provenance(algorithm=other).cache_key


@pytest.mark.parametrize("settings", DEFAULTS, ids=lambda s: type(s).__name__)
def test_record_is_json_serializable(settings):
    record = json.loads(json.dumps(_provenance(settings).record()))
    assert record["settings"]["kind"] == settings.kind.value
    assert record["time_range"] == [0.0, 0.3]
    assert record["channels"] == {"mode": "single", "channel": 0}
    assert record["algorithm"] == {"id": "test.algorithm", "version": "1"}


def test_record_encodes_combined_channels_as_null():
    record = _provenance(channels=ChannelPolicy.combined()).record()
    assert record["channels"] == {"mode": "combined", "channel": None}


def test_equal_numeric_settings_share_cache_key():
    assert (
        _provenance(PitchSettings(pitch_floor=50)).cache_key
        == _provenance(PitchSettings()).cache_key
    )


def test_channel_mode_must_be_enum():
    with pytest.raises(InvalidInput):
        ChannelPolicy("combined")


# ---------------------------------------------------------------------------
# Spectrogram


def test_spectrogram_builds_and_freezes_arrays():
    s = Spectrogram(**_spectrogram_kwargs())
    assert s.power.shape == (3, 3)
    assert s.valid.dtype == np.bool_
    np.testing.assert_array_equal(s.valid[:, 2], [False, False, False])


def test_spectrogram_owns_its_input_arrays():
    kwargs = _spectrogram_kwargs()
    s = Spectrogram(**kwargs)
    kwargs["power"][0, 0] = 99.0
    assert s.power[0, 0] == 1.0


def test_spectrogram_arrays_are_read_only():
    s = Spectrogram(**_spectrogram_kwargs())
    assert not s.power.flags.writeable
    assert not s.valid.flags.writeable
    with pytest.raises(ValueError):
        s.power[0, 0] = 5.0
    with pytest.raises(ValueError):
        s.valid[0, 0] = False


def test_spectrogram_rejects_power_shape_mismatch():
    kwargs = _spectrogram_kwargs()
    kwargs["power"] = np.zeros((3, 2))
    with pytest.raises(InvalidInput):
        Spectrogram(**kwargs)


def test_spectrogram_rejects_valid_shape_mismatch():
    kwargs = _spectrogram_kwargs()
    kwargs["valid"] = np.ones((2, 3), dtype=bool)
    with pytest.raises(InvalidInput):
        Spectrogram(**kwargs)


def test_spectrogram_invalid_cell_must_be_nan():
    kwargs = _spectrogram_kwargs()
    kwargs["power"][0, 2] = 1.0
    with pytest.raises(InvalidInput):
        Spectrogram(**kwargs)


@pytest.mark.parametrize("bad", [-1.0, math.inf, -math.inf, NAN])
def test_spectrogram_valid_power_must_be_finite_and_non_negative(bad):
    kwargs = _spectrogram_kwargs()
    kwargs["power"][0, 0] = bad
    with pytest.raises(InvalidInput):
        Spectrogram(**kwargs)


def test_spectrogram_rejects_pitch_provenance():
    kwargs = _spectrogram_kwargs()
    kwargs["provenance"] = _provenance(PitchSettings())
    with pytest.raises(InvalidInput):
        Spectrogram(**kwargs)


def test_spectrogram_rejects_non_provenance_object():
    kwargs = _spectrogram_kwargs()
    kwargs["provenance"] = None
    with pytest.raises(InvalidInput):
        Spectrogram(**kwargs)


# ---------------------------------------------------------------------------
# Pitch


def test_pitch_builds_with_documented_layout():
    p = Pitch(**_pitch_kwargs())
    assert np.isnan(p.candidate_frequency[1, 0])
    assert p.candidate_strength[1, 0] == 0.5
    assert np.isnan(p.candidate_frequency[0, 2])
    assert np.isnan(p.candidate_strength[0, 2])


def test_pitch_frequency_is_nan_where_unvoiced():
    p = Pitch(**_pitch_kwargs())
    np.testing.assert_array_equal(p.frequency, [200.0, NAN, NAN, 220.0])


def test_pitch_frequency_is_nan_at_or_above_ceiling():
    p = Pitch(**_pitch_kwargs())
    # The candidate itself stays recorded; only the derived F0 is cleared.
    assert p.candidate_frequency[2, 0] == 5000.0
    assert np.isnan(p.frequency[2])


def test_pitch_voicing_and_strength():
    p = Pitch(**_pitch_kwargs())
    np.testing.assert_array_equal(p.voiced, [True, False, False, True])
    np.testing.assert_allclose(p.strength, [0.9, 0.5, 0.7, 0.3])


def test_pitch_arrays_are_read_only():
    p = Pitch(**_pitch_kwargs())
    for array in (p.intensity, p.candidate_frequency, p.candidate_strength, p.candidate_count):
        assert not array.flags.writeable
    with pytest.raises(ValueError):
        p.candidate_frequency[0, 0] = 1.0


def test_pitch_rejects_chosen_index_past_count():
    kwargs = _pitch_kwargs()
    kwargs["chosen"] = np.array([0, 2, 0, 2], dtype=np.int64)
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


def test_pitch_rejects_zero_count():
    kwargs = _pitch_kwargs()
    kwargs["candidate_count"] = np.array([0, 2, 1, 3], dtype=np.int64)
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


def test_pitch_rejects_count_beyond_slots():
    kwargs = _pitch_kwargs()
    kwargs["candidate_count"] = np.array([2, 2, 1, 4], dtype=np.int64)
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


def test_pitch_rejects_non_nan_unused_frequency():
    kwargs = _pitch_kwargs()
    kwargs["candidate_frequency"][0, 2] = 100.0
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


def test_pitch_rejects_non_nan_unused_strength():
    kwargs = _pitch_kwargs()
    kwargs["candidate_strength"][2, 2] = 0.1
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


@pytest.mark.parametrize("bad", [0.0, -100.0, math.inf, -math.inf])
def test_pitch_rejects_non_positive_or_infinite_candidate(bad):
    kwargs = _pitch_kwargs()
    kwargs["candidate_frequency"][1, 1] = bad
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


def test_pitch_rejects_non_finite_used_strength():
    kwargs = _pitch_kwargs()
    kwargs["candidate_strength"][0, 0] = NAN
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


@pytest.mark.parametrize("bad", [1.2, -0.1, NAN])
def test_pitch_intensity_must_lie_in_unit_interval(bad):
    kwargs = _pitch_kwargs()
    kwargs["intensity"] = np.array([0.2, bad, 1.0, 0.8])
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


def test_pitch_counts_must_be_integers():
    kwargs = _pitch_kwargs()
    kwargs["candidate_count"] = np.array([2.0, 2.0, 1.0, 3.0])
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


def test_pitch_chosen_must_be_integers():
    kwargs = _pitch_kwargs()
    kwargs["chosen"] = np.array([0.0, 0.0, 0.0, 2.0])
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


def test_pitch_rejects_wrong_kind_provenance():
    kwargs = _pitch_kwargs()
    kwargs["provenance"] = _provenance(FormantSettings())
    with pytest.raises(InvalidInput):
        Pitch(**kwargs)


# ---------------------------------------------------------------------------
# Formant


def test_formant_builds_and_exposes_counts():
    f = Formant(**_formant_kwargs())
    np.testing.assert_array_equal(f.count, [2, 0, 3])


def test_formant_zero_count_frame_is_allowed():
    f = Formant(**_formant_kwargs())
    assert f.count[1] == 0
    assert np.isnan(f.frequency[1]).all()
    assert np.isnan(f.bandwidth[1]).all()


def test_formant_arrays_are_read_only():
    f = Formant(**_formant_kwargs())
    for array in (f.frequency, f.bandwidth, f.count, f.intensity):
        assert not array.flags.writeable
    with pytest.raises(ValueError):
        f.frequency[0, 0] = 1.0


def test_formant_rejects_unsorted_frequencies():
    kwargs = _formant_kwargs()
    kwargs["frequency"][2] = [2300.0, 300.0, 3000.0]
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


def test_formant_rejects_non_nan_unused_frequency():
    kwargs = _formant_kwargs()
    kwargs["frequency"][0, 2] = 1000.0
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


def test_formant_rejects_non_nan_unused_bandwidth():
    kwargs = _formant_kwargs()
    kwargs["bandwidth"][1, 0] = 10.0
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


def test_formant_rejects_negative_bandwidth():
    kwargs = _formant_kwargs()
    kwargs["bandwidth"][2, 0] = -1.0
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


@pytest.mark.parametrize("bad", [0.0, -300.0, math.inf])
def test_formant_rejects_non_positive_frequency(bad):
    kwargs = _formant_kwargs()
    kwargs["frequency"][0, 0] = bad
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


@pytest.mark.parametrize("bad", [[2, 0, 4], [-1, 0, 3]])
def test_formant_counts_must_lie_in_slot_range(bad):
    kwargs = _formant_kwargs()
    kwargs["count"] = np.array(bad, dtype=np.int64)
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


def test_formant_counts_must_be_integers():
    kwargs = _formant_kwargs()
    kwargs["count"] = np.array([2.0, 0.0, 3.0])
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


def test_formant_rejects_negative_intensity():
    kwargs = _formant_kwargs()
    kwargs["intensity"] = np.array([1e-4, -1.0, 0.5])
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


def test_formant_rejects_wrong_kind_provenance():
    kwargs = _formant_kwargs()
    kwargs["provenance"] = _provenance(PitchSettings())
    with pytest.raises(InvalidInput):
        Formant(**kwargs)


# ---------------------------------------------------------------------------
# Intensity


def test_intensity_nan_is_undefined_and_not_valid():
    result = _intensity()
    np.testing.assert_array_equal(result.valid, [True, False, True])
    assert np.isnan(result.values[1])


def test_intensity_values_are_read_only():
    result = _intensity()
    assert not result.values.flags.writeable
    with pytest.raises(ValueError):
        result.values[0] = 0.0


@pytest.mark.parametrize("bad", [math.inf, -math.inf])
def test_intensity_rejects_infinite_values(bad):
    with pytest.raises(InvalidInput):
        _intensity(values=np.array([60.0, bad, 80.0]))


def test_intensity_rejects_wrong_length():
    with pytest.raises(InvalidInput):
        _intensity(values=np.array([60.0, 70.0]))


def test_intensity_rejects_wrong_kind_provenance():
    with pytest.raises(InvalidInput):
        _intensity(provenance=_provenance(SpectrogramSettings()))


# ---------------------------------------------------------------------------
# accept()


def test_accept_true_for_matching_identity():
    result = _intensity(provenance=_provenance(IntensitySettings(), token=3))
    assert accept(result, "rec", 1, 3)


@pytest.mark.parametrize(
    "source_id, revision, token",
    [("other", 1, 3), ("rec", 2, 3), ("rec", 1, 0)],
)
def test_accept_false_when_any_identity_field_differs(source_id, revision, token):
    result = _intensity(provenance=_provenance(IntensitySettings(), token=3))
    assert not accept(result, source_id, revision, token)


# ---------------------------------------------------------------------------
# Review #14: result frames must lie in the requested range; canonical bounds


def _outside_axis():
    return SampledAxis(10.0, 11.0, 1, 0.1, 10.5)


def test_misaligned_intensity_is_rejected_codex_reproduction():
    request = AnalysisRequest(
        "rec", 1, IntensitySettings(), TimeRange(0.0, 1.0), ChannelPolicy.single(0), token=3
    )
    with pytest.raises(InvalidInput):
        Intensity(Provenance(request, ALGO), _outside_axis(), np.array([60.0]))


def test_misaligned_spectrogram_is_rejected():
    with pytest.raises(InvalidInput):
        Spectrogram(
            _provenance(SpectrogramSettings()),
            _outside_axis(),
            SPEC_FREQ,
            np.ones((1, 3)),
            np.ones((1, 3), dtype=bool),
        )


def test_misaligned_pitch_is_rejected():
    with pytest.raises(InvalidInput):
        Pitch(
            _provenance(PitchSettings()),
            _outside_axis(),
            500.0,
            np.array([0.5]),
            np.array([[200.0]]),
            np.array([[0.9]]),
            np.array([1]),
            np.array([0]),
        )


def test_misaligned_formant_is_rejected():
    with pytest.raises(InvalidInput):
        Formant(
            _provenance(FormantSettings()),
            _outside_axis(),
            np.array([1.0]),
            np.array([1]),
            np.array([[500.0]]),
            np.array([[80.0]]),
        )


def _intensity_on(axis, start=0.0, end=0.3):
    provenance = _provenance(IntensitySettings(), time_range=TimeRange(start, end))
    return Intensity(provenance, axis, np.full(axis.nx, 60.0))


def test_frame_at_range_start_is_inside():
    _intensity_on(SampledAxis(0.0, 0.3, 3, 0.1, 0.0))


def test_frame_at_range_end_is_outside_half_open():
    with pytest.raises(InvalidInput):
        _intensity_on(SampledAxis(0.0, 0.3, 4, 0.1, 0.0))  # last centre 0.3 == end


def test_frame_within_tolerance_of_start_counts_as_on_it():
    _intensity_on(SampledAxis(-1.0, 0.3, 3, 0.1, -1e-12))


def test_frame_just_before_start_is_rejected():
    with pytest.raises(InvalidInput):
        _intensity_on(SampledAxis(-1.0, 0.3, 3, 0.1, -1e-6))


def test_axis_domain_may_extend_beyond_range():
    # Window padding: declared domain wider than the range, frame centres inside it.
    _intensity_on(SampledAxis(-0.5, 0.8, 3, 0.1, 0.05))


def test_equivalent_bounds_share_cache_key():
    a = _provenance(time_range=TimeRange(0, 1)).cache_key
    b = _provenance(time_range=TimeRange(0.0, 1.0)).cache_key
    c = _provenance(time_range=TimeRange(-0.0, 1.0)).cache_key
    assert a == b == c


def test_time_range_bounds_are_canonical_floats():
    r = TimeRange(-0.0, 1)
    assert type(r.start) is float and type(r.end) is float
    assert math.copysign(1.0, r.start) == 1.0


# Review #14 (second pass): malformed request fields fail at construction


@pytest.mark.parametrize("time_range", [[0, 1], (0.0, 1.0), None, {"start": 0, "end": 1}])
def test_request_rejects_non_time_range(time_range):
    with pytest.raises(InvalidInput):
        AnalysisRequest("source", 0, PitchSettings(), time_range, ChannelPolicy.single(0))


@pytest.mark.parametrize("channels", [{"channel": 0}, None, 0, "combined"])
def test_request_rejects_non_channel_policy(channels):
    with pytest.raises(InvalidInput):
        AnalysisRequest("source", 0, PitchSettings(), TimeRange(0.0, 1.0), channels)
