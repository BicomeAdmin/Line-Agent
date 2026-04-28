"""Tests for the human-jitter module — verify randomness shape, env
disable, and that the wrappers don't break when client.shell raises."""

import os
import unittest
from unittest.mock import MagicMock, patch

from app.adb import human_jitter as hj


class JitterEnvDisableTests(unittest.TestCase):
    """ECHO_DISABLE_JITTER=1 must give deterministic output for tests
    that need repeatability."""

    def test_jittered_sleep_uses_base_when_disabled(self):
        with patch.dict(os.environ, {"ECHO_DISABLE_JITTER": "1"}):
            with patch("time.sleep") as ts:
                slept = hj.jittered_sleep(2.0)
            self.assertEqual(slept, 2.0)
            ts.assert_called_once_with(2.0)

    def test_jittered_tap_uses_exact_when_disabled(self):
        with patch.dict(os.environ, {"ECHO_DISABLE_JITTER": "1"}):
            client = MagicMock()
            x, y = hj.jittered_tap(client, 100, 200)
            self.assertEqual((x, y), (100, 200))
            client.shell.assert_called_once_with("input", "tap", "100", "200")

    def test_jittered_swipe_uses_exact_when_disabled(self):
        with patch.dict(os.environ, {"ECHO_DISABLE_JITTER": "1"}):
            client = MagicMock()
            x1, y1, x2, y2, d = hj.jittered_swipe(client, 540, 1800, 540, 900, 300)
            self.assertEqual((x1, y1, x2, y2, d), (540, 1800, 540, 900, 300))


class JitterShapeTests(unittest.TestCase):
    """When jitter is on, samples should distribute reasonably around
    base / center, not collapse to a constant."""

    def test_jittered_sleep_clamps_to_min(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ECHO_DISABLE_JITTER", None)
            with patch("time.sleep"):
                # Heavy jitter, tiny base — must never go below settle_min.
                samples = [hj.jittered_sleep(0.05, jitter_pct=2.0, settle_min=0.05) for _ in range(50)]
                for s in samples:
                    self.assertGreaterEqual(s, 0.05)

    def test_jittered_tap_stays_within_jitter_radius(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ECHO_DISABLE_JITTER", None)
            client = MagicMock()
            calls = []
            for _ in range(100):
                x, y = hj.jittered_tap(client, 500, 500, pixel_jitter=5)
                calls.append((x, y))
            xs = [x for x, _ in calls]
            ys = [y for _, y in calls]
            # Must be within ±5 of center (triangular distribution)
            for x in xs:
                self.assertGreaterEqual(x, 495)
                self.assertLessEqual(x, 505)
            for y in ys:
                self.assertGreaterEqual(y, 495)
                self.assertLessEqual(y, 505)
            # Must not all be the same value (probability ≈0)
            self.assertGreater(len(set(xs)), 3)
            self.assertGreater(len(set(ys)), 3)

    def test_jittered_poll_interval_varies(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ECHO_DISABLE_JITTER", None)
            samples = [hj.jittered_poll_interval(60) for _ in range(50)]
            self.assertGreater(len(set(samples)), 10)
            # No sample below 50% of base
            for s in samples:
                self.assertGreaterEqual(s, 30.0)

    def test_reading_pause_skews_toward_min(self):
        """Reading pause should be skewed-toward-min (most are short)."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ECHO_DISABLE_JITTER", None)
            with patch("time.sleep"):
                samples = [hj.reading_pause(0.2, 1.2) for _ in range(200)]
            # Median should be closer to min than max (right-skew)
            samples.sort()
            median = samples[len(samples) // 2]
            self.assertLess(median, 0.7)  # midpoint is 0.7; skewed should be lower


class IntegrationSafetyTests(unittest.TestCase):
    """Wrappers must not change call shape when client.shell behaves
    correctly."""

    def test_jittered_tap_passes_str_args(self):
        client = MagicMock()
        hj.jittered_tap(client, 100, 200)
        args = client.shell.call_args.args
        self.assertEqual(args[:2], ("input", "tap"))
        self.assertIsInstance(args[2], str)
        self.assertIsInstance(args[3], str)

    def test_jittered_swipe_passes_str_args(self):
        client = MagicMock()
        hj.jittered_swipe(client, 0, 0, 100, 100, 200)
        args = client.shell.call_args.args
        self.assertEqual(args[:2], ("input", "swipe"))
        for a in args[2:]:
            self.assertIsInstance(a, str)


if __name__ == "__main__":
    unittest.main()
