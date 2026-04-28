"""Anti-fingerprinting: replace clockwork-precise automation cadence
with human-shaped randomness.

Why: a real person doesn't poll a chat every 60.000s, doesn't tap the
exact pixel center of a button, doesn't sleep precisely 800 ms between
operations. Bots do. LINE's anti-automation heuristics (and any future
ones) reward variance. Roadmap Tier 1 #2.

What this module gives you:
  - jittered_sleep(base, jitter_pct, *, settle_min): replace
    `time.sleep(base)` with a Gaussian-around-base wait, clamped so it
    never goes below `settle_min` (UI animations need a floor).
  - jittered_tap(client, x, y, *, pixel_jitter): replace `input tap x y`
    with `input tap x±jitter y±jitter`. Buttons are big enough that
    ±5 px never misses the target but breaks the perfect-center pattern.
  - reading_pause(): a deliberate 200-1200 ms wait to simulate the
    "human glances at the new screen before acting" beat between
    navigation steps.
  - jittered_poll_interval(base): adapter for daemon poll intervals
    (60s ±25% etc.) — apply to the (now - last_check) comparison.

All randomness is Gaussian-clamped, not uniform: that better matches
real human reaction-time distributions and pixel-tap accuracy. Cite-
worthy reading: Fitts's law for tap accuracy, ex-Gaussian for keystroke
timing — but for our purposes Gaussian is enough.

The module is import-safe with no LINE / ADB dependencies of its own
(it just wraps `client.shell(...)` calls passed in). Disable globally
by setting ECHO_DISABLE_JITTER=1 — restores deterministic behavior
for repeatable testing.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any


def _jitter_disabled() -> bool:
    return os.getenv("ECHO_DISABLE_JITTER", "").strip() == "1"


def jittered_sleep(
    base_seconds: float,
    *,
    jitter_pct: float = 0.25,
    settle_min: float = 0.1,
) -> float:
    """Sleep around `base_seconds` with Gaussian noise of stddev =
    base * jitter_pct. Clamped to [settle_min, base * (1 + 2*jitter_pct)].

    Returns the actual time slept, for trace logging if useful.
    """

    if _jitter_disabled():
        time.sleep(base_seconds)
        return base_seconds

    sigma = max(0.01, base_seconds * jitter_pct)
    actual = random.gauss(base_seconds, sigma)
    upper = base_seconds * (1.0 + 2.0 * jitter_pct)
    actual = max(settle_min, min(upper, actual))
    time.sleep(actual)
    return actual


def reading_pause(min_seconds: float = 0.2, max_seconds: float = 1.2) -> float:
    """Pause as if the user is reading the new screen before next action.

    Use between navigation transitions where the human eye would naturally
    take a beat — e.g., after tapping into a chat, before scanning content;
    after typing in a search box, before tapping the result.
    """

    if _jitter_disabled():
        time.sleep((min_seconds + max_seconds) / 2)
        return (min_seconds + max_seconds) / 2

    # Skewed-toward-min: most reading pauses are short, occasionally long.
    # Use uniform-cubed for a simple right-skew.
    u = random.random() ** 2
    actual = min_seconds + u * (max_seconds - min_seconds)
    time.sleep(actual)
    return actual


def jittered_tap(
    client: Any,
    x: int,
    y: int,
    *,
    pixel_jitter: int = 5,
) -> tuple[int, int]:
    """Tap (x, y) with ±pixel_jitter on each axis. Returns the actual
    coordinates used, for trace logging.

    Buttons in LINE OpenChat are typically ≥40 px tall, so ±5 px never
    misses while breaking the pixel-perfect-center signature.
    """

    if _jitter_disabled():
        client.shell("input", "tap", str(x), str(y))
        return x, y

    # Triangular distribution — most taps near center, occasionally near edges.
    dx = int(round(random.triangular(-pixel_jitter, pixel_jitter, 0)))
    dy = int(round(random.triangular(-pixel_jitter, pixel_jitter, 0)))
    actual_x, actual_y = max(1, x + dx), max(1, y + dy)
    client.shell("input", "tap", str(actual_x), str(actual_y))
    return actual_x, actual_y


def jittered_swipe(
    client: Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int,
    *,
    pixel_jitter: int = 8,
    duration_jitter_pct: float = 0.30,
) -> tuple[int, int, int, int, int]:
    """Swipe with start/end pixel jitter and randomized duration.

    A perfectly straight, fixed-speed swipe is highly bot-like. We add
    noise to start, end, and duration. Note: this is still a straight
    swipe — full Bezier curve is Tier 2 #10. ±8 px on endpoints + duration
    jitter alone defeats simple kinematic detectors.
    """

    if _jitter_disabled():
        client.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))
        return x1, y1, x2, y2, duration_ms

    nx1 = max(1, x1 + random.randint(-pixel_jitter, pixel_jitter))
    ny1 = max(1, y1 + random.randint(-pixel_jitter, pixel_jitter))
    nx2 = max(1, x2 + random.randint(-pixel_jitter, pixel_jitter))
    ny2 = max(1, y2 + random.randint(-pixel_jitter, pixel_jitter))
    sigma = max(10, duration_ms * duration_jitter_pct)
    nd = max(80, int(random.gauss(duration_ms, sigma)))
    client.shell("input", "swipe", str(nx1), str(ny1), str(nx2), str(ny2), str(nd))
    return nx1, ny1, nx2, ny2, nd


def bezier_swipe(
    client: Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int,
    *,
    n_steps: int = 12,
    curve_strength: float = 0.25,
    pixel_jitter: int = 4,
) -> tuple[int, int, int, int, int]:
    """Curved swipe via `input motionevent DOWN/MOVE/UP` chain.

    A real human's finger doesn't move in a perfectly straight line —
    there's always small lateral deviation that follows a curved
    trajectory. Standard `input swipe` produces a perfect straight line
    at constant speed; this function instead samples N points along a
    quadratic Bezier curve (with a randomized control point pulled to
    one side) and emits one MOVE per sample, time-spaced via
    duration_ms / n_steps.

    Bezier curve:
        P(t) = (1-t)² · P0 + 2t(1-t) · CTRL + t² · P1
    where CTRL is offset perpendicular to the P0→P1 line by a random
    fraction (curve_strength) of the swipe length.

    Falls back to standard `jittered_swipe` if `input motionevent`
    isn't supported (older Android API < 24, some custom ROMs).
    Per Roadmap Tier 2 #5 — completes the anti-fingerprint trio
    started by T1.2.

    Returns the actual endpoints + n_steps used, for trace logging.
    """

    if _jitter_disabled():
        client.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))
        return x1, y1, x2, y2, n_steps

    # Add slight endpoint noise (already in jittered_swipe but we
    # re-do here so callers can opt for bezier directly).
    nx1 = max(1, x1 + random.randint(-pixel_jitter, pixel_jitter))
    ny1 = max(1, y1 + random.randint(-pixel_jitter, pixel_jitter))
    nx2 = max(1, x2 + random.randint(-pixel_jitter, pixel_jitter))
    ny2 = max(1, y2 + random.randint(-pixel_jitter, pixel_jitter))

    # Compute Bezier control point: midpoint, perpendicular offset.
    mid_x, mid_y = (nx1 + nx2) / 2, (ny1 + ny2) / 2
    dx, dy = nx2 - nx1, ny2 - ny1
    # Perpendicular vector (rotated 90°)
    perp_x, perp_y = -dy, dx
    perp_len = (perp_x ** 2 + perp_y ** 2) ** 0.5 or 1.0
    perp_x, perp_y = perp_x / perp_len, perp_y / perp_len
    # Offset signed by random sign × strength × stroke length
    stroke_len = (dx ** 2 + dy ** 2) ** 0.5
    offset = random.uniform(-curve_strength, curve_strength) * stroke_len
    ctrl_x = mid_x + perp_x * offset
    ctrl_y = mid_y + perp_y * offset

    # Sample N points along quadratic Bezier, with eased timing
    # (slow at start/end, faster in middle — natural human motion).
    step_sleep = max(0.005, duration_ms / 1000.0 / n_steps)

    try:
        # Initial DOWN at start
        client.shell("input", "motionevent", "DOWN", str(nx1), str(ny1))
        for i in range(1, n_steps):
            t = i / n_steps
            # Ease in/out: smooth-step
            t_eased = t * t * (3 - 2 * t)
            x = (1 - t_eased) ** 2 * nx1 + 2 * t_eased * (1 - t_eased) * ctrl_x + t_eased ** 2 * nx2
            y = (1 - t_eased) ** 2 * ny1 + 2 * t_eased * (1 - t_eased) * ctrl_y + t_eased ** 2 * ny2
            client.shell("input", "motionevent", "MOVE", str(int(x)), str(int(y)))
            time.sleep(step_sleep)
        # Final UP at end
        client.shell("input", "motionevent", "UP", str(nx2), str(ny2))
    except Exception:  # noqa: BLE001
        # Fallback to standard swipe if motionevent fails (older API).
        client.shell("input", "swipe", str(nx1), str(ny1), str(nx2), str(ny2), str(duration_ms))

    return nx1, ny1, nx2, ny2, n_steps


def jittered_poll_interval(base_seconds: float, jitter_pct: float = 0.25) -> float:
    """Compute the actual interval to wait before next poll. Apply this
    to the threshold comparison in scheduler tick:

        if (now - last_check) < jittered_poll_interval(base):
            skip
    """

    if _jitter_disabled():
        return base_seconds
    sigma = base_seconds * jitter_pct
    actual = random.gauss(base_seconds, sigma)
    return max(base_seconds * 0.5, actual)
