"""Tests for the pure-math core of audio_fidelity_mcp (no heavy backends needed)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audio_fidelity_mcp as af  # noqa: E402


def test_db_matches_definition():
    assert af._db(1.0) == pytest.approx(0.0)
    assert af._db(100.0) == pytest.approx(20.0)
    assert af._db(0.0) == pytest.approx(-120.0)  # EPS floor


def test_estimate_delay_recovers_fractional_delay():
    sr = 16000
    rng = np.random.default_rng(0)
    t = np.arange(int(4.0 * sr)) / sr
    # band-limited noise: no strong periodicity, so PHAT picks the true lag
    x = np.convolve(rng.standard_normal(len(t)), np.ones(8) / 8, mode="same")
    x -= x.mean()
    delay_true = 123.4
    # build the copy delayed by 124 samples, then advance it by 0.6 -> net 123.4
    y = np.zeros((len(x), 1))
    y[124:, 0] = x[: len(x) - 124] + 0.3 * rng.standard_normal(len(x) - 124)
    y = af._shift(y, 0.6)
    ref = x[:, None]
    measured = af._estimate_delay(ref, y, sr, max_lag_s=1.0, window_s=3.0)
    assert measured == pytest.approx(delay_true, abs=0.15)


def test_estimate_delay_handles_stereo():
    sr = 8000
    rng = np.random.default_rng(1)
    x = np.convolve(rng.standard_normal(2 * sr), np.ones(6) / 6, mode="same")
    x -= x.mean()
    ref = np.stack([x, x], axis=1)
    test = np.roll(ref, 500, axis=0)
    measured = af._estimate_delay(ref, test, sr, max_lag_s=0.5, window_s=1.5)
    assert measured == pytest.approx(500, abs=1.0)


def test_shift_preserves_length_and_moves_energy():
    sr = 8000
    t = np.arange(sr) / sr
    x = np.sin(2 * np.pi * 440 * t)[:, None]
    late = af._shift(x, -50.0)          # negative delay = test is late by 50 samples
    assert len(late) == len(x)
    # late[60 + i] == x[10 + i]
    x0, l0 = x[10:7950, 0], late[60:8000, 0]
    assert np.corrcoef(x0, l0)[0, 1] > 0.99


def test_verdict_ladder():
    assert af._verdict(True, 0, 0, 100).startswith("IDENTICAL")
    assert "transparent" in af._verdict(False, 0.2, -25, 40)
    assert "Subtle" in af._verdict(False, 3, -5, 30)
    assert "Audible" in af._verdict(False, 10, 0, 20)
    assert "strong" in af._verdict(False, 90, 20, 2)


def test_odg_and_mos_labels():
    assert af._odg_label(-0.1).startswith("imperceptible")
    assert af._odg_label(-3.0).startswith("annoying")
    assert af._odg_label(-3.9).startswith("very annoying")
    assert af._mos_label(4.7).startswith("transparent")
    assert af._mos_label(3.6) == "good"
    assert af._mos_label(1.5) == "bad"


def test_platform_tag_format():
    assert "-" in af._platform_tag()


def test_shift_negative_delay_keeps_alignment():
    sr = 16000
    rng = np.random.default_rng(2)
    x = np.convolve(rng.standard_normal(2 * sr), np.ones(8) / 8, mode="same")
    x -= x.mean()
    early = af._shift(x[:, None], -50.0)      # copy arrives 50 samples LATE
    assert len(early) == len(x[:, None])
    measured = af._estimate_delay(x[:, None], early, sr, max_lag_s=0.2, window_s=1.0)
    assert measured == pytest.approx(50.0, abs=0.2)
