from __future__ import annotations

from pathlib import Path

from app.config import settings


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def workspace_root() -> Path:
    return WORKSPACE_ROOT


def customers_root() -> Path:
    return WORKSPACE_ROOT / "customers"


def customer_root(customer_id: str) -> Path:
    return customers_root() / customer_id


def customer_data_root(customer_id: str) -> Path:
    return customer_root(customer_id) / "data"


def runtime_root() -> Path:
    target = WORKSPACE_ROOT / ".project_echo"
    target.mkdir(parents=True, exist_ok=True)
    return target


def jobs_state_path() -> Path:
    return runtime_root() / "jobs.jsonl"


def reviews_state_path() -> Path:
    return runtime_root() / "reviews.jsonl"


def calibrations_state_path() -> Path:
    return runtime_root() / "calibrations.jsonl"


def raw_xml_dir(customer_id: str) -> Path:
    return customer_data_root(customer_id) / "raw_xml"


def scheduled_posts_dir(customer_id: str) -> Path:
    target = customer_data_root(customer_id) / "scheduled_posts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def scheduled_posts_path(customer_id: str, community_id: str) -> Path:
    return scheduled_posts_dir(customer_id) / f"{community_id}.json"


def voice_profiles_dir(customer_id: str) -> Path:
    target = customer_root(customer_id) / "voice_profiles"
    target.mkdir(parents=True, exist_ok=True)
    return target


def voice_profile_path(customer_id: str, community_id: str) -> Path:
    return voice_profiles_dir(customer_id) / f"{community_id}.md"


def watches_state_path(customer_id: str) -> Path:
    target = customer_data_root(customer_id) / "watches.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def audit_log_path(customer_id: str) -> Path:
    return customer_data_root(customer_id) / "audit.jsonl"


def ensure_customer_directories(customer_id: str) -> None:
    for path in (
        raw_xml_dir(customer_id),
        customer_data_root(customer_id) / "cleaned_messages",
        customer_data_root(customer_id) / "prompts",
        customer_data_root(customer_id) / "llm_outputs",
        customer_data_root(customer_id) / "send_logs",
    ):
        path.mkdir(parents=True, exist_ok=True)


def default_raw_xml_path(customer_id: str | None = None) -> Path:
    active_customer = customer_id or settings.default_customer_id
    ensure_customer_directories(active_customer)
    return raw_xml_dir(active_customer) / "latest.xml"
