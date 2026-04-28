import unittest

from app.adb.devices import _extract_package_from_activity_line


class OpenPackageHelpersTests(unittest.TestCase):
    def test_extract_package_from_top_resumed_activity_with_activity_suffix(self) -> None:
        line = "topResumedActivity=ActivityRecord{123 u0 com.android.settings/.Settings t12}"
        self.assertEqual(_extract_package_from_activity_line(line), "com.android.settings")


if __name__ == "__main__":
    unittest.main()
