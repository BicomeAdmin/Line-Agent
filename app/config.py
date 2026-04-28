from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    adb_path: str = os.getenv("ECHO_ADB_PATH", "adb")
    android_sdk_root: str = os.getenv("ANDROID_SDK_ROOT", "/opt/homebrew/share/android-commandlinetools")
    default_customer_id: str = os.getenv("ECHO_DEFAULT_CUSTOMER_ID", "customer_a")
    require_human_approval: bool = os.getenv("ECHO_REQUIRE_HUMAN_APPROVAL", "true").lower() == "true"
    line_apk_path: str | None = os.getenv("ECHO_LINE_APK_PATH")
    lark_verification_token: str | None = os.getenv("LARK_VERIFICATION_TOKEN")
    lark_app_id: str | None = os.getenv("LARK_APP_ID")
    lark_app_secret: str | None = os.getenv("LARK_APP_SECRET")
    llm_enabled: bool = os.getenv("ECHO_LLM_ENABLED", "false").lower() == "true"
    llm_provider: str = os.getenv("ECHO_LLM_PROVIDER", "anthropic")
    llm_model: str = os.getenv("ECHO_LLM_MODEL", "claude-haiku-4-5")
    llm_max_tokens: int = int(os.getenv("ECHO_LLM_MAX_TOKENS", "400"))
    anthropic_api_key: str | None = os.getenv("ECHO_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY")


settings = Settings()
