from __future__ import annotations

from dataclasses import dataclass
from datetime import time as day_time
from pathlib import Path

from app.core.calibrations import calibration_store
from app.core.risk_control import RiskControl
from app.core.simple_yaml import load_yaml
from app.storage.paths import customer_root, workspace_root


@dataclass(frozen=True)
class DeviceConfig:
    device_id: str
    label: str
    customer_id: str
    enabled: bool = True
    avd_name: str | None = None


@dataclass(frozen=True)
class CustomerConfig:
    customer_id: str
    display_name: str
    allowed_operators: tuple[str, ...]
    default_persona: str


@dataclass(frozen=True)
class CommunityConfig:
    customer_id: str
    community_id: str
    display_name: str
    persona: str
    device_id: str
    patrol_interval_minutes: int
    enabled: bool = True
    input_x: int | None = None
    input_y: int | None = None
    send_x: int | None = None
    send_y: int | None = None
    coordinate_source: str = "missing"
    invite_url: str | None = None
    group_id: str | None = None
    # Operator's display name in THIS community. Required for accurate
    # self-detection when scoring chat-export-derived messages (where
    # we don't have the runtime is_self flag from the LINE UI parser).
    # E.g. operator might be "比利" in 愛美星 but "山寶" in 山納百景.
    operator_nickname: str | None = None
    # Additional operator identities active in this community — e.g. an
    # internal test account ("阿樂 本尊") used alongside the primary one
    # ("阿樂2"). reply_target_selector treats every alias the same as
    # operator_nickname so the bot doesn't suggest replying to its own
    # historical messages. Empty by default.
    operator_aliases: tuple[str, ...] = ()
    # Per-community opt-in: at start_hour TPE, scheduler auto-starts a watch
    # that runs until end_hour. Default OFF — operator opts in per community
    # by setting auto_watch.enabled: true in the community YAML.
    auto_watch_enabled: bool = False
    auto_watch_start_hour_tpe: int = 10
    auto_watch_end_hour_tpe: int = 22
    auto_watch_duration_minutes: int = 720
    auto_watch_cooldown_seconds: int = 600
    auto_watch_poll_interval_seconds: int = 60


def load_devices_config() -> list[DeviceConfig]:
    payload = _load_yaml_file(workspace_root() / "configs" / "devices.yaml")
    devices = payload.get("devices", [])
    return [
        DeviceConfig(
            device_id=str(item["device_id"]),
            label=str(item.get("label", item["device_id"])),
            customer_id=str(item["customer_id"]),
            enabled=bool(item.get("enabled", True)),
            avd_name=str(item["avd_name"]) if isinstance(item.get("avd_name"), str) and item.get("avd_name") else None,
        )
        for item in devices
    ]


def get_device_config(device_id: str) -> DeviceConfig:
    for device in load_devices_config():
        if device.device_id == device_id:
            return device
    raise ValueError(f"Unknown device_id: {device_id}")


def load_risk_control() -> RiskControl:
    payload = _load_yaml_file(workspace_root() / "configs" / "risk_control.yaml")
    activity = payload.get("activity_window", {})
    send_delay = payload.get("send_delay_seconds", {})
    return RiskControl(
        fixed_ip_mode=bool(payload.get("fixed_ip_mode", True)),
        activity_start=_parse_clock(str(activity.get("start", "09:00"))),
        activity_end=_parse_clock(str(activity.get("end", "23:00"))),
        min_send_delay_seconds=float(send_delay.get("min", 5)),
        max_send_delay_seconds=float(send_delay.get("max", 30)),
        account_cooldown_seconds=int(payload.get("account_cooldown_seconds", 900)),
        community_cooldown_seconds=int(payload.get("community_cooldown_seconds", 1800)),
        require_human_approval=bool(payload.get("require_human_approval", True)),
    )


def load_customer_config(customer_id: str) -> CustomerConfig:
    payload = _load_yaml_file(customer_root(customer_id) / "customer.yaml")
    operators = payload.get("allowed_operators", [])
    return CustomerConfig(
        customer_id=str(payload.get("customer_id", customer_id)),
        display_name=str(payload.get("display_name", customer_id)),
        allowed_operators=tuple(str(item) for item in operators),
        default_persona=str(payload.get("default_persona", "default")),
    )


def load_community_config(customer_id: str, community_id: str) -> CommunityConfig:
    payload = _load_yaml_file(customer_root(customer_id) / "communities" / f"{community_id}.yaml")
    config = CommunityConfig(
        customer_id=customer_id,
        community_id=str(payload.get("community_id", community_id)),
        display_name=str(payload.get("display_name", community_id)),
        persona=str(payload.get("persona", "default")),
        device_id=str(payload["device_id"]),
        patrol_interval_minutes=int(payload.get("patrol_interval_minutes", 120)),
        enabled=bool(payload.get("enabled", True)),
        input_x=_optional_int(payload.get("input_x")),
        input_y=_optional_int(payload.get("input_y")),
        send_x=_optional_int(payload.get("send_x")),
        send_y=_optional_int(payload.get("send_y")),
        invite_url=str(payload["invite_url"]) if isinstance(payload.get("invite_url"), str) and payload.get("invite_url") else None,
        group_id=str(payload["group_id"]) if isinstance(payload.get("group_id"), str) and payload.get("group_id") else None,
        operator_nickname=str(payload["operator_nickname"]).strip() if isinstance(payload.get("operator_nickname"), str) and payload.get("operator_nickname") else None,
        operator_aliases=_parse_operator_aliases(payload.get("operator_aliases")),
        **_parse_auto_watch(payload.get("auto_watch")),
    )
    return _apply_runtime_calibration(config)


def _parse_operator_aliases(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
    return tuple(out)


def _parse_auto_watch(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, object] = {}
    if "enabled" in value:
        out["auto_watch_enabled"] = bool(value["enabled"])
    for key, dest in (
        ("start_hour_tpe", "auto_watch_start_hour_tpe"),
        ("end_hour_tpe", "auto_watch_end_hour_tpe"),
        ("duration_minutes", "auto_watch_duration_minutes"),
        ("cooldown_seconds", "auto_watch_cooldown_seconds"),
        ("poll_interval_seconds", "auto_watch_poll_interval_seconds"),
    ):
        if key in value and value[key] is not None:
            try:
                out[dest] = int(value[key])
            except (TypeError, ValueError):
                continue
    return out


def load_communities_for_device(device_id: str) -> list[CommunityConfig]:
    communities: list[CommunityConfig] = []
    for device in load_devices_config():
        if device.device_id != device_id:
            continue
        community_dir = customer_root(device.customer_id) / "communities"
        for path in sorted(community_dir.glob("*.yaml")):
            community = load_community_config(device.customer_id, path.stem)
            if community.device_id == device_id and community.enabled:
                communities.append(community)
    return communities


def load_all_communities() -> list[CommunityConfig]:
    communities: list[CommunityConfig] = []
    for device in load_devices_config():
        community_dir = customer_root(device.customer_id) / "communities"
        for path in sorted(community_dir.glob("*.yaml")):
            community = load_community_config(device.customer_id, path.stem)
            if community.enabled:
                communities.append(community)
    return communities


def _load_yaml_file(path: Path) -> dict[str, object]:
    value = load_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected mapping in {path}")
    return value


def _parse_clock(value: str) -> day_time:
    hour_text, minute_text = value.split(":", 1)
    return day_time(int(hour_text), int(minute_text))


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _apply_runtime_calibration(config: CommunityConfig) -> CommunityConfig:
    runtime = calibration_store.get(config.customer_id, config.community_id)
    if runtime is not None:
        return _rebuild_community_config(
            config,
            input_x=runtime.input_x,
            input_y=runtime.input_y,
            send_x=runtime.send_x,
            send_y=runtime.send_y,
            coordinate_source=runtime.source,
        )

    if None not in (config.input_x, config.input_y, config.send_x, config.send_y):
        return _rebuild_community_config(
            config,
            input_x=config.input_x,
            input_y=config.input_y,
            send_x=config.send_x,
            send_y=config.send_y,
            coordinate_source="file",
        )

    return config


def _rebuild_community_config(
    config: CommunityConfig,
    *,
    input_x: int | None,
    input_y: int | None,
    send_x: int | None,
    send_y: int | None,
    coordinate_source: str,
) -> CommunityConfig:
    """Rebuild CommunityConfig with new coordinate fields, preserving every
    other field — especially auto_watch_*. Centralized so future field
    additions to CommunityConfig only need to be threaded through here once,
    not in each calibration branch."""

    return CommunityConfig(
        customer_id=config.customer_id,
        community_id=config.community_id,
        display_name=config.display_name,
        persona=config.persona,
        device_id=config.device_id,
        patrol_interval_minutes=config.patrol_interval_minutes,
        enabled=config.enabled,
        input_x=input_x,
        input_y=input_y,
        send_x=send_x,
        send_y=send_y,
        coordinate_source=coordinate_source,
        invite_url=config.invite_url,
        group_id=config.group_id,
        operator_nickname=config.operator_nickname,
        operator_aliases=config.operator_aliases,
        auto_watch_enabled=config.auto_watch_enabled,
        auto_watch_start_hour_tpe=config.auto_watch_start_hour_tpe,
        auto_watch_end_hour_tpe=config.auto_watch_end_hour_tpe,
        auto_watch_duration_minutes=config.auto_watch_duration_minutes,
        auto_watch_cooldown_seconds=config.auto_watch_cooldown_seconds,
        auto_watch_poll_interval_seconds=config.auto_watch_poll_interval_seconds,
    )
