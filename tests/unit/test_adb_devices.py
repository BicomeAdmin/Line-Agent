import unittest

from app.adb.devices import _extract_package_from_activity_line


class AdbDeviceTests(unittest.TestCase):
    def test_extract_package_from_top_resumed_activity(self) -> None:
        line = "topResumedActivity=ActivityRecord{123 u0 com.google.android.apps.nexuslauncher/.NexusLauncherActivity t12}"
        self.assertEqual(_extract_package_from_activity_line(line), "com.google.android.apps.nexuslauncher")

    def test_extract_package_from_resumed_activity(self) -> None:
        line = "mResumedActivity: ActivityRecord{abc u0 jp.naver.line.android/.activity.SplashActivity t10}"
        self.assertEqual(_extract_package_from_activity_line(line), "jp.naver.line.android")

    def test_extract_package_returns_none_without_activity_token(self) -> None:
        self.assertIsNone(_extract_package_from_activity_line("random text"))


if __name__ == "__main__":
    unittest.main()
