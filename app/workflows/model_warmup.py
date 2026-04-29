"""One-shot model preloader for long-running services (scheduler_daemon).

Why: cold-loading BGE embedding (95 MB, ~5-9 s) and Chinese-Emotion (~400 MB,
~3-4 s) inside short-lived MCP child processes was killing the codex/MCP
transport on first call (Transport closed). When the daemon owns the models
in-process, the cost is paid once at boot and every subsequent watch tick
is fast.

Safe to call multiple times — both get_* helpers cache singletons.
Safe to call when transformers/torch aren't installed — get_* helpers
return None gracefully and we just log it.
"""

from __future__ import annotations

import time


def warm_up_models() -> dict[str, object]:
    """Eagerly load embedding + emotion singletons. Returns timing/status."""

    out: dict[str, object] = {}

    t = time.time()
    try:
        from app.ai.embedding_service import get_embedding_service
        svc = get_embedding_service()
        out["embedding"] = {
            "loaded": svc is not None,
            "elapsed_s": round(time.time() - t, 2),
        }
    except Exception as exc:  # noqa: BLE001
        out["embedding"] = {"loaded": False, "error": str(exc)[:120]}

    t = time.time()
    try:
        from app.ai.emotion_classifier import get_emotion_classifier
        clf = get_emotion_classifier()
        out["emotion"] = {
            "loaded": clf is not None,
            "elapsed_s": round(time.time() - t, 2),
        }
    except Exception as exc:  # noqa: BLE001
        out["emotion"] = {"loaded": False, "error": str(exc)[:120]}

    return out
