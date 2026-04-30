import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.workflows.scheduled_post_recurrence import (
    RecurrenceError,
    bump_fired,
    next_occurrence,
    normalize_recurrence,
    parse_recurrence_string,
)

TPE = ZoneInfo("Asia/Taipei")


def _tpe_epoch(year: int, month: int, day: int, hour: int, minute: int) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=TPE).timestamp()


class NormalizeRecurrenceTests(unittest.TestCase):
    def test_once_returns_none(self) -> None:
        self.assertIsNone(normalize_recurrence({"kind": "once"}))

    def test_none_returns_none(self) -> None:
        self.assertIsNone(normalize_recurrence(None))

    def test_daily_minimal(self) -> None:
        rec = normalize_recurrence({"kind": "daily", "time_tpe": "8:0"})
        self.assertEqual(rec["kind"], "daily")
        self.assertEqual(rec["time_tpe"], "08:00")
        self.assertEqual(rec["occurrences_fired"], 0)

    def test_weekly_requires_weekday(self) -> None:
        with self.assertRaises(RecurrenceError):
            normalize_recurrence({"kind": "weekly", "time_tpe": "20:00"})
        rec = normalize_recurrence({"kind": "weekly", "time_tpe": "20:00", "weekday": "MON"})
        self.assertEqual(rec["weekday"], "mon")

    def test_monthly_requires_day_of_month_in_range(self) -> None:
        with self.assertRaises(RecurrenceError):
            normalize_recurrence({"kind": "monthly", "time_tpe": "10:00", "day_of_month": 30})
        rec = normalize_recurrence({"kind": "monthly", "time_tpe": "10:00", "day_of_month": 15})
        self.assertEqual(rec["day_of_month"], 15)

    def test_until_iso_must_have_tz(self) -> None:
        with self.assertRaises(RecurrenceError):
            normalize_recurrence({
                "kind": "daily", "time_tpe": "20:00", "until_iso": "2026-12-31T23:59:00",
            })

    def test_invalid_time_format(self) -> None:
        with self.assertRaises(RecurrenceError):
            normalize_recurrence({"kind": "daily", "time_tpe": "8AM"})

    def test_invalid_kind(self) -> None:
        with self.assertRaises(RecurrenceError):
            normalize_recurrence({"kind": "yearly", "time_tpe": "20:00"})


class ParseRecurrenceStringTests(unittest.TestCase):
    def test_daily(self) -> None:
        rec = parse_recurrence_string("daily@08:00")
        self.assertEqual(rec["kind"], "daily")
        self.assertEqual(rec["time_tpe"], "08:00")
        self.assertEqual(rec["occurrences_fired"], 0)

    def test_weekly(self) -> None:
        rec = parse_recurrence_string("weekly:mon@20:00")
        self.assertEqual(rec["kind"], "weekly")
        self.assertEqual(rec["weekday"], "mon")
        self.assertEqual(rec["time_tpe"], "20:00")

    def test_monthly(self) -> None:
        rec = parse_recurrence_string("monthly:5@10:30")
        self.assertEqual(rec["kind"], "monthly")
        self.assertEqual(rec["day_of_month"], 5)
        self.assertEqual(rec["time_tpe"], "10:30")

    def test_once(self) -> None:
        self.assertIsNone(parse_recurrence_string("once"))

    def test_invalid(self) -> None:
        with self.assertRaises(RecurrenceError):
            parse_recurrence_string("yearly@20:00")


class NextOccurrenceTests(unittest.TestCase):
    def test_daily_advances_one_day(self) -> None:
        # Post sent on 2026-05-04 20:00 TPE; next daily 20:00 is 2026-05-05 20:00
        sent_at = _tpe_epoch(2026, 5, 4, 20, 0)
        rec = parse_recurrence_string("daily@20:00")
        result = next_occurrence(rec, after_epoch=sent_at)
        self.assertIsNotNone(result)
        next_epoch, next_iso = result
        expected = _tpe_epoch(2026, 5, 5, 20, 0)
        self.assertEqual(next_epoch, expected)
        self.assertIn("+08:00", next_iso)

    def test_weekly_advances_to_next_weekday(self) -> None:
        # Sent Mon 2026-05-04 20:00 TPE; next weekly Mon 20:00 is 2026-05-11
        sent_at = _tpe_epoch(2026, 5, 4, 20, 0)
        rec = parse_recurrence_string("weekly:mon@20:00")
        result = next_occurrence(rec, after_epoch=sent_at)
        next_epoch, _ = result
        self.assertEqual(next_epoch, _tpe_epoch(2026, 5, 11, 20, 0))

    def test_weekly_finds_weekday_within_week(self) -> None:
        # Sent Mon, weekly Wed → next is two days later
        sent_at = _tpe_epoch(2026, 5, 4, 20, 0)
        rec = parse_recurrence_string("weekly:wed@20:00")
        result = next_occurrence(rec, after_epoch=sent_at)
        self.assertEqual(result[0], _tpe_epoch(2026, 5, 6, 20, 0))

    def test_monthly_advances_to_next_month(self) -> None:
        sent_at = _tpe_epoch(2026, 5, 1, 10, 0)
        rec = parse_recurrence_string("monthly:1@10:00")
        result = next_occurrence(rec, after_epoch=sent_at)
        self.assertEqual(result[0], _tpe_epoch(2026, 6, 1, 10, 0))

    def test_monthly_year_rollover(self) -> None:
        sent_at = _tpe_epoch(2026, 12, 15, 10, 0)
        rec = parse_recurrence_string("monthly:15@10:00")
        result = next_occurrence(rec, after_epoch=sent_at)
        self.assertEqual(result[0], _tpe_epoch(2027, 1, 15, 10, 0))

    def test_until_iso_terminates(self) -> None:
        sent_at = _tpe_epoch(2026, 5, 4, 20, 0)
        rec = normalize_recurrence({
            "kind": "daily", "time_tpe": "20:00",
            "until_iso": "2026-05-05T19:00:00+08:00",  # before next 20:00
        })
        self.assertIsNone(next_occurrence(rec, after_epoch=sent_at))

    def test_max_occurrences_terminates(self) -> None:
        sent_at = _tpe_epoch(2026, 5, 4, 20, 0)
        rec = normalize_recurrence({
            "kind": "daily", "time_tpe": "20:00", "max_occurrences": 1,
        })
        rec["occurrences_fired"] = 1
        self.assertIsNone(next_occurrence(rec, after_epoch=sent_at))

    def test_once_returns_none(self) -> None:
        self.assertIsNone(next_occurrence(None, after_epoch=0.0))


class SafetyCapTests(unittest.TestCase):
    """Unbounded recurrence applies a safety cap — protects against
    operator typos that would otherwise book years of posts."""

    def test_daily_unbounded_caps_at_90(self) -> None:
        rec = normalize_recurrence({"kind": "daily", "time_tpe": "08:00"})
        self.assertEqual(rec["max_occurrences"], 90)
        self.assertTrue(rec["max_occurrences_was_defaulted"])

    def test_weekly_unbounded_caps_at_52(self) -> None:
        rec = normalize_recurrence({"kind": "weekly", "time_tpe": "20:00", "weekday": "mon"})
        self.assertEqual(rec["max_occurrences"], 52)
        self.assertTrue(rec["max_occurrences_was_defaulted"])

    def test_monthly_unbounded_caps_at_24(self) -> None:
        rec = normalize_recurrence({"kind": "monthly", "time_tpe": "10:00", "day_of_month": 1})
        self.assertEqual(rec["max_occurrences"], 24)
        self.assertTrue(rec["max_occurrences_was_defaulted"])

    def test_explicit_max_occurrences_skips_cap(self) -> None:
        rec = normalize_recurrence({
            "kind": "daily", "time_tpe": "08:00", "max_occurrences": 999,
        })
        self.assertEqual(rec["max_occurrences"], 999)
        self.assertNotIn("max_occurrences_was_defaulted", rec)

    def test_until_iso_skips_cap(self) -> None:
        rec = normalize_recurrence({
            "kind": "daily", "time_tpe": "08:00",
            "until_iso": "2027-01-01T00:00:00+08:00",
        })
        self.assertNotIn("max_occurrences", rec)
        self.assertNotIn("max_occurrences_was_defaulted", rec)


class BumpFiredTests(unittest.TestCase):
    def test_increments(self) -> None:
        rec = parse_recurrence_string("daily@20:00")
        self.assertEqual(rec["occurrences_fired"], 0)
        bumped = bump_fired(rec)
        self.assertEqual(bumped["occurrences_fired"], 1)
        # Original untouched
        self.assertEqual(rec["occurrences_fired"], 0)

    def test_none_passthrough(self) -> None:
        self.assertIsNone(bump_fired(None))


if __name__ == "__main__":
    unittest.main()
