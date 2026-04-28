import unittest

from app.lark.commands import parse_command


class LarkCommandTests(unittest.TestCase):
    def test_parse_system_status(self) -> None:
        command = parse_command("請回報系統狀態")
        self.assertEqual(command.action, "system_status")

    def test_parse_device_status(self) -> None:
        command = parse_command("查詢 emulator-5554 裝置狀態")
        self.assertEqual(command.action, "device_status")
        self.assertEqual(command.device_id, "emulator-5554")

    def test_parse_readiness_status(self) -> None:
        command = parse_command("請做部署檢查")
        self.assertEqual(command.action, "readiness_status")

    def test_parse_calibration_status(self) -> None:
        command = parse_command("請回報校準狀態")
        self.assertEqual(command.action, "calibration_status")

    def test_parse_community_status(self) -> None:
        command = parse_command("請回報 openchat_001 社群狀態")
        self.assertEqual(command.action, "community_status")
        self.assertEqual(command.community_id, "openchat_001")

    def test_parse_acceptance_status(self) -> None:
        command = parse_command("請幫我做 openchat_001 驗收檢查")
        self.assertEqual(command.action, "acceptance_status")
        self.assertEqual(command.community_id, "openchat_001")

    def test_parse_draft_reply_natural_verbs(self) -> None:
        for phrase in [
            "請幫忙在 openchat_002 說話 emulator-5554",
            "請幫我在 openchat_002 開口 emulator-5554",
            "請在 openchat_002 接話 emulator-5554",
            "幫忙說一下 openchat_002 emulator-5554",
        ]:
            command = parse_command(phrase)
            self.assertEqual(command.action, "draft_reply", msg=f"failed for: {phrase}")
            self.assertEqual(command.community_id, "openchat_002", msg=f"failed for: {phrase}")
            self.assertEqual(command.device_id, "emulator-5554", msg=f"failed for: {phrase}")

    def test_parse_draft_reply_wins_over_read_chat(self) -> None:
        # "對話" alone routes to read_chat, but explicit draft verbs are checked first.
        command = parse_command("請在 openchat_002 接話分析對話 emulator-5554")
        self.assertEqual(command.action, "draft_reply")

    def test_parse_prepare_line_session(self) -> None:
        command = parse_command("請幫 emulator-5554 準備LINE")
        self.assertEqual(command.action, "prepare_line_session")
        self.assertEqual(command.device_id, "emulator-5554")

    def test_parse_ensure_device_ready(self) -> None:
        command = parse_command("請幫 emulator-5554 修復裝置")
        self.assertEqual(command.action, "ensure_device_ready")
        self.assertEqual(command.device_id, "emulator-5554")

    def test_parse_install_line_app(self) -> None:
        command = parse_command("請幫 emulator-5554 安裝LINE")
        self.assertEqual(command.action, "install_line_app")
        self.assertEqual(command.device_id, "emulator-5554")

    def test_parse_line_apk_status(self) -> None:
        command = parse_command("請回報 LINE APK 狀態")
        self.assertEqual(command.action, "line_apk_status")

    def test_parse_project_snapshot(self) -> None:
        command = parse_command("請回報 openchat_001 專案快照")
        self.assertEqual(command.action, "project_snapshot")
        self.assertEqual(command.community_id, "openchat_001")

    def test_parse_action_queue(self) -> None:
        command = parse_command("請回報 openchat_001 行動隊列")
        self.assertEqual(command.action, "action_queue")
        self.assertEqual(command.community_id, "openchat_001")

    def test_parse_milestone_status(self) -> None:
        command = parse_command("請回報 openchat_001 里程碑狀態")
        self.assertEqual(command.action, "milestone_status")
        self.assertEqual(command.community_id, "openchat_001")

    def test_parse_openchat_validation(self) -> None:
        command = parse_command("請幫我做 openchat_001 OpenChat 驗證")
        self.assertEqual(command.action, "openchat_validation")
        self.assertEqual(command.community_id, "openchat_001")

    def test_parse_read_chat_limit(self) -> None:
        command = parse_command("抓取 emulator-5554 最近 15 筆訊息")
        self.assertEqual(command.action, "read_chat")
        self.assertEqual(command.limit, 15)

    def test_parse_suggest_category(self) -> None:
        command = parse_command("請推薦母嬰產品")
        self.assertEqual(command.action, "suggest")
        self.assertEqual(command.category, "maternity")


if __name__ == "__main__":
    unittest.main()
