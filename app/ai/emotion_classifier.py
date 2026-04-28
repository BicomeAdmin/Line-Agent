"""8-class Chinese emotion classifier — Johnson8187/Chinese-Emotion.

Replaces the regex-only pain/broadcast detection with a real
multiclass classifier so we can:

  - Boost reply priority on 疑惑 (puzzled) — Paul's "create value"
    moment when bot can be helpful.
  - Boost reply priority on 悲傷 (sad) — caring response builds trust
    long-term, even though it's not a "fix it" answer.
  - DOWN-rank reply on 憤怒 (angry) — replying to an angry message
    in real-time often escalates. Better to surface to operator
    explicitly via a "needs your attention" flag and let them decide.
  - Surface neutral baseline (平淡) without false-positive scoring.

8 labels (empirically mapped against the model):
  LABEL_0 → 平淡 (neutral)
  LABEL_1 → 關切 (caring/concerned)
  LABEL_2 → 開心 (happy)
  LABEL_3 → 憤怒 (angry)
  LABEL_4 → 悲傷 (sad)
  LABEL_5 → 疑惑 (puzzled / questioning)
  LABEL_6 → 驚奇 (surprised)
  LABEL_7 → 厭惡 (disgust / loathing)

Singleton, lazy-loaded. ~400 MB model, ~100 ms/sentence on Apple
Silicon CPU. Graceful degradation when transformers isn't installed.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any


_LOCK = threading.Lock()
_INSTANCE: "EmotionClassifier | None" = None
_INIT_FAILED = False
_TEST_OVERRIDE: "EmotionClassifier | None" = None


DEFAULT_MODEL = os.getenv("ECHO_EMOTION_MODEL", "Johnson8187/Chinese-Emotion")


# Empirically-determined label mapping (see commit message + tests).
# The model card on HuggingFace doesn't expose id2label so we ship our
# own mapping after live verification on canonical examples.
_LABEL_MAP = {
    "LABEL_0": "neutral",
    "LABEL_1": "caring",
    "LABEL_2": "happy",
    "LABEL_3": "angry",
    "LABEL_4": "sad",
    "LABEL_5": "puzzled",
    "LABEL_6": "surprise",
    "LABEL_7": "disgust",
}

# Chinese display labels (for surfacing to operator in zh-TW).
_LABEL_ZH = {
    "neutral": "平淡",
    "caring": "關切",
    "happy": "開心",
    "angry": "憤怒",
    "sad": "悲傷",
    "puzzled": "疑惑",
    "surprise": "驚奇",
    "disgust": "厭惡",
}


class EmotionClassifier:
    """Wraps a transformers text-classification pipeline."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        from transformers import pipeline  # type: ignore
        self.model_name = model_name
        self._clf = pipeline("text-classification", model=model_name)
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_max = 1024

    def classify(self, text: str) -> dict[str, Any]:
        """Return {label, label_zh, score} for the highest-confidence class.

        Empty / very short text → returns neutral with score=0 so callers
        can simply check label != 'neutral' to act.
        """

        text = (text or "").strip()
        if not text or len(text) < 2:
            return {"label": "neutral", "label_zh": "平淡", "score": 0.0}
        if text in self._cache:
            return self._cache[text]
        try:
            result = self._clf(text)[0]
            raw_label = result["label"]
            label = _LABEL_MAP.get(raw_label, "neutral")
            payload = {
                "label": label,
                "label_zh": _LABEL_ZH.get(label, label),
                "score": float(result["score"]),
            }
        except Exception:  # noqa: BLE001
            payload = {"label": "neutral", "label_zh": "平淡", "score": 0.0}
        if len(self._cache) >= self._cache_max:
            keys = list(self._cache.keys())[: self._cache_max // 10]
            for k in keys:
                self._cache.pop(k, None)
        self._cache[text] = payload
        return payload


def get_emotion_classifier() -> "EmotionClassifier | None":
    """Process-wide singleton, or None if transformers isn't installed."""

    global _INSTANCE, _INIT_FAILED

    if _TEST_OVERRIDE is not None:
        return _TEST_OVERRIDE
    if _INSTANCE is not None:
        return _INSTANCE
    if _INIT_FAILED:
        return None

    with _LOCK:
        if _INSTANCE is not None:
            return _INSTANCE
        if _INIT_FAILED:
            return None
        try:
            t0 = time.time()
            _INSTANCE = EmotionClassifier()
            elapsed = time.time() - t0
            print(f"[emotion] loaded {_INSTANCE.model_name} in {elapsed:.1f}s", flush=True)
        except ImportError:
            _INIT_FAILED = True
            print("[emotion] transformers not installed; emotion scoring disabled", flush=True)
            return None
        except Exception as exc:  # noqa: BLE001
            _INIT_FAILED = True
            print(f"[emotion] init failed: {exc!r}; emotion scoring disabled", flush=True)
            return None
    return _INSTANCE


def set_test_classifier(stub: "EmotionClassifier | None") -> None:
    global _TEST_OVERRIDE
    _TEST_OVERRIDE = stub
