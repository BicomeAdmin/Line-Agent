"""Regression test for #8 fingerprint contamination on KOC ranking.

Without the alias-aware operator filter, the operator's aliased name
(e.g. '阿樂 本尊' with role badge) would dominate in_degree /
betweenness centrality and surface as the top KOC candidate of their
own community — bizarre and confusing.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import relationship_graph as rg
from app.storage.config_loader import CommunityConfig


def _fake_community(
    operator_nickname: str | None = None,
    operator_aliases: tuple[str, ...] = (),
) -> CommunityConfig:
    return CommunityConfig(
        customer_id="customer_a",
        community_id="openchat_test",
        display_name="測試群",
        persona="default",
        device_id="emulator-5554",
        patrol_interval_minutes=60,
        operator_nickname=operator_nickname,
        operator_aliases=operator_aliases,
    )


def _build_export(lines: list[tuple[str, str, str]]) -> str:
    """date / time / sender_msg → LINE export text."""
    out: list[str] = []
    last_date = None
    for date_str, time_str, sender_msg in lines:
        if date_str != last_date:
            out.append(f"{date_str.replace('-', '.')} 星期一")
            last_date = date_str
        out.append(f"{time_str} {sender_msg}")
    return "\n".join(out) + "\n"


class KocFiltersAliasedOperatorTests(unittest.TestCase):
    def test_aliased_operator_excluded_from_koc(self) -> None:
        community = _fake_community(
            operator_nickname="阿樂2",
            operator_aliases=("阿樂 本尊",),
        )
        # Operator (阿樂 本尊) has the most replies — would normally
        # dominate KOC ranking. With aliases honored, they're excluded.
        export = _build_export([
            ("2026-04-26", "10:00", "alice 嗨"),
            ("2026-04-26", "10:01", "阿樂 本尊 回 alice"),
            ("2026-04-26", "10:02", "bob 也來"),
            ("2026-04-26", "10:03", "阿樂 本尊 回 bob"),
            ("2026-04-26", "10:04", "carol 哈"),
            ("2026-04-26", "10:05", "阿樂 本尊 回 carol"),
        ])

        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "export.txt"
            export_path.write_text(export, encoding="utf-8")
            with patch.object(rg, "load_community_config", lambda *_: community), \
                 patch.object(rg, "latest_export_path", lambda *_: export_path), \
                 patch.object(rg, "graph_snapshot_path",
                              lambda *_: Path(tmp_dir) / "snap.json"), \
                 patch.object(rg, "append_audit_event", lambda *a, **k: None):
                result = rg.build_relationship_graph("customer_a", "openchat_test")

        self.assertEqual(result["status"], "ok")
        koc_senders = [c["sender"] for c in result.get("koc_candidates", [])]
        # Aliased operator must NOT appear in KOC candidates
        self.assertNotIn("阿樂 本尊", koc_senders)
        self.assertNotIn("阿樂2", koc_senders)


if __name__ == "__main__":
    unittest.main()
