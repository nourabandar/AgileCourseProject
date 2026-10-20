"""Run with: python -m unittest discover -s tests -v

Every test uses a temporary database. The classroom demo database is never opened.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app import RIYADH, create_app, get_available_slots, get_db, now_riyadh


class PageElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.radios = []
        self.date_links = []
        self.field_names = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag in ("input", "select", "textarea"):
            self.field_names.append(attributes.get("name"))
        if tag == "input" and attributes.get("type") == "radio":
            self.radios.append(attributes["value"])
        if tag == "a" and "date-option" in attributes.get("class", "").split():
            self.date_links.append(parse_qs(urlparse(attributes["href"]).query)["date"][0])


class AppointmentTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory(prefix="university-appointments-test-")
        self.addCleanup(self.temp_directory.cleanup)
        self.database = str(Path(self.temp_directory.name) / "test.db")
        self.config = {"TESTING": True, "DATABASE": self.database, "SECRET_KEY": "test-only-session-key"}
        self.today = now_riyadh().date()
        self.clock = patch("app.now_riyadh", return_value=datetime.combine(self.today, time(8, 15), RIYADH))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.future = self.today + timedelta(days=2)
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def query(self, sql, parameters=()):
        with self.app.app_context():
            return get_db().execute(sql, parameters).fetchall()

    def add_hours(self, start="09:00", end="12:00", day=None):
        day = self.future.weekday() if day is None else day
        return self.client.post("/staff/availability", data={
            "weekday": str(day), "start_time": start, "end_time": end,
        })

    def add_block(self, start="10:00", end="11:00", day=None, all_day=False):
        data = {"date": (day or self.future).isoformat(), "start_time": start, "end_time": end}
        if all_day:
            data["all_day"] = "1"
        return self.client.post("/staff/blocks", data=data)

    def book(self, slot="09:00", reason="Discuss course selection", day=None, **extra):
        data = {"date": (day or self.future).isoformat(), "time": slot, "reason": reason}
        data.update(extra)
        return self.client.post("/student/book", data=data)

    def slot_times(self, day=None):
        with self.app.app_context():
            return [slot["time"] for slot in get_available_slots(get_db(), day or self.future)]

    def student_html(self, day=None):
        return self.client.get("/student", query_string={"date": (day or self.future).isoformat()}).get_data(as_text=True)

    def test_empty_first_launch_and_main_pages(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        student = self.client.get("/student")
        staff = self.client.get("/staff")
        self.assertEqual(student.status_code, 200)
        self.assertEqual(staff.status_code, 200)
        self.assertIn(b"No available times. Please choose another date.", student.data)
        self.assertIn(b"No dates have available times in the next 14 days", student.data)
        self.assertIn(b"disabled>Book Appointment", student.data)
        self.assertIn(b"No weekly availability saved", staff.data)
        self.assertIn(b"No blocked periods saved", staff.data)
        self.assertEqual(len(self.query("SELECT * FROM staff")), 1)
        self.assertEqual(len(self.query("SELECT * FROM students")), 1)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 0)
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"):
            self.assertIn(day.encode(), staff.data)
        with self.client.get("/static/style.css") as stylesheet:
            self.assertEqual(stylesheet.status_code, 200)

    def test_save_weekly_availability_and_expected_slots(self):
        self.assertEqual(self.add_hours().status_code, 303)
        expected = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"]
        self.assertEqual(self.slot_times(), expected)
        self.assertEqual(PageElements(self.student_html()).radios, expected)
        self.assertEqual(len(self.query("SELECT * FROM weekly_availability")), 1)

    def test_multiple_days_separate_and_adjacent_ranges(self):
        self.assertEqual(self.add_hours("09:00", "10:00").status_code, 303)
        self.assertEqual(self.add_hours("10:00", "11:00").status_code, 303)
        self.assertEqual(self.add_hours("13:00", "14:00").status_code, 303)
        other_day = (self.future.weekday() + 1) % 7
        self.assertEqual(self.add_hours("09:00", "10:00", other_day).status_code, 303)
        self.assertEqual(self.slot_times(), ["09:00", "09:30", "10:00", "10:30", "13:00", "13:30"])
        self.assertEqual(len(self.slot_times()), len(set(self.slot_times())))

    def test_edit_availability(self):
        self.add_hours()
        range_id = self.query("SELECT id FROM weekly_availability")[0]["id"]
        page = self.client.get("/staff", query_string={"edit": range_id})
        self.assertIn(b"Save Changes", page.data)
        self.assertIn(b'value="09:00"', page.data)
        response = self.client.post(f"/staff/availability/{range_id}/edit", data={
            "weekday": str(self.future.weekday()), "start_time": "10:00", "end_time": "11:00",
        })
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.slot_times(), ["10:00", "10:30"])

    def test_delete_availability(self):
        self.add_hours()
        range_id = self.query("SELECT id FROM weekly_availability")[0]["id"]
        self.assertEqual(self.client.post(f"/staff/availability/{range_id}/delete").status_code, 303)
        self.assertEqual(self.slot_times(), [])

    def test_reject_invalid_availability(self):
        cases = [
            ("0", "10:00", "09:00"), ("0", "09:00", "09:00"),
            ("0", "23:00", "01:00"), ("0", "09:15", "10:00"),
            ("0", "09:00", "10:15"), ("0", "25:00", "26:00"),
            ("0", "09:60", "10:00"), ("0", "9:00", "10:00"),
            ("0", "", "10:00"), ("0", "09:00:00", "10:00"),
            ("7", "09:00", "10:00"), ("-1", "09:00", "10:00"),
            ("Monday", "09:00", "10:00"), ("", "09:00", "10:00"),
        ]
        for weekday, start, end in cases:
            with self.subTest(weekday=weekday, start=start, end=end):
                response = self.client.post("/staff/availability", data={
                    "weekday": weekday, "start_time": start, "end_time": end,
                })
                self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM weekly_availability")), 0)

    def test_reject_duplicate_and_overlapping_availability(self):
        self.add_hours()
        for start, end in [("09:00", "12:00"), ("08:30", "09:30"), ("11:30", "13:00"),
                           ("10:00", "11:00"), ("08:00", "13:00")]:
            with self.subTest(start=start, end=end):
                response = self.add_hours(start, end)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"overlaps or duplicates", response.data)
        self.assertEqual(len(self.query("SELECT * FROM weekly_availability")), 1)

    def test_reject_edit_that_overlaps_another_range(self):
        self.add_hours("09:00", "10:00")
        self.add_hours("11:00", "12:00")
        range_id = self.query("SELECT id FROM weekly_availability ORDER BY start_minute")[0]["id"]
        response = self.client.post(f"/staff/availability/{range_id}/edit", data={
            "weekday": str(self.future.weekday()), "start_time": "09:00", "end_time": "11:30",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.slot_times(), ["09:00", "09:30", "11:00", "11:30"])

    def test_out_of_range_entry_ids_are_handled(self):
        for entry_id in ["0", "-1", "999999999999999999999999999999999"]:
            for path in [f"/staff/availability/{entry_id}/delete", f"/staff/blocks/{entry_id}/delete",
                         f"/staff/availability/{entry_id}/edit"]:
                with self.subTest(path=path):
                    self.assertEqual(self.client.post(path).status_code, 404)

    def test_partial_day_block(self):
        self.add_hours()
        self.assertEqual(self.add_block().status_code, 303)
        self.assertEqual(self.slot_times(), ["09:00", "09:30", "11:00", "11:30"])

    def test_block_overlapping_part_of_a_slot_removes_whole_slot(self):
        self.add_hours()
        self.assertEqual(self.add_block("09:15", "09:45").status_code, 303)
        self.assertEqual(self.slot_times(), ["10:00", "10:30", "11:00", "11:30"])

    def test_full_day_block(self):
        self.add_hours()
        self.assertEqual(self.add_block(all_day=True).status_code, 303)
        self.assertEqual(self.slot_times(), [])
        block = self.query("SELECT * FROM blocked_periods")[0]
        self.assertEqual((block["start_minute"], block["end_minute"], block["all_day"]), (0, 1440, 1))
        self.assertIn(b"All day", self.client.get("/staff").data)

    def test_unblock_restores_slots(self):
        self.add_hours()
        self.add_block()
        block_id = self.query("SELECT id FROM blocked_periods")[0]["id"]
        self.assertEqual(self.client.post(f"/staff/blocks/{block_id}/delete").status_code, 303)
        self.assertEqual(self.slot_times(), ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"])

    def test_reject_duplicate_overlapping_blocks_and_allow_adjacent(self):
        self.add_block()
        for start, end in [("10:00", "11:00"), ("09:45", "10:15"), ("10:15", "10:30"), ("09:00", "12:00")]:
            with self.subTest(start=start, end=end):
                response = self.add_block(start, end)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"overlaps or duplicates", response.data)
        self.assertEqual(self.add_block("11:00", "12:00").status_code, 303)
        self.assertEqual(self.add_block(all_day=True).status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM blocked_periods")), 2)

    def test_reject_invalid_block_dates_and_times(self):
        for invalid_date in ["", "not-a-date", f"{self.today.year + 1}-02-30", "2027-2-01",
                             (self.today - timedelta(days=1)).isoformat()]:
            with self.subTest(date=invalid_date):
                response = self.client.post("/staff/blocks", data={"date": invalid_date, "all_day": "1"})
                self.assertEqual(response.status_code, 400)
        for start, end in [("", ""), ("10:00", "09:00"), ("10:00", "10:00"),
                           ("24:00", "25:00"), ("23:30", "00:30"), ("09:00", "09:99")]:
            with self.subTest(start=start, end=end):
                self.assertEqual(self.add_block(start, end).status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM blocked_periods")), 0)

    def test_exclude_past_slots_using_riyadh_time(self):
        self.add_hours("08:00", "10:00", self.today.weekday())
        self.assertEqual(self.slot_times(self.today), ["08:30", "09:00", "09:30"])
        with patch("app.now_riyadh", return_value=datetime.combine(self.today, time(8, 30), RIYADH)):
            self.assertEqual(self.slot_times(self.today), ["09:00", "09:30"])
        self.assertEqual(self.slot_times(self.today - timedelta(days=1)), [])
        self.assertEqual(self.book(slot="08:00", day=self.today).status_code, 400)

    def test_reject_past_or_invalid_student_dates(self):
        for value in [(self.today - timedelta(days=1)).isoformat(), f"{self.today.year + 1}-02-30", "wrong", ""]:
            with self.subTest(date=value):
                page = self.client.get("/student", query_string={"date": value})
                self.assertEqual(page.status_code, 200)
                self.assertIn(b'role="alert"', page.data)
                self.assertEqual(PageElements(page.get_data(as_text=True)).radios, [])
                response = self.client.post("/student/book", data={"date": value, "time": "09:00", "reason": "Advice"})
                self.assertEqual(response.status_code, 400)

    def test_save_booking_trim_reason_and_use_fixed_server_identities(self):
        self.add_hours()
        response = self.book(reason="  Discuss my courses. \n", staff_id="9999", student_id="9999")
        self.assertEqual(response.status_code, 303)
        booking = self.query("SELECT * FROM appointments")[0]
        self.assertEqual(booking["reason"], "Discuss my courses.")
        self.assertEqual((booking["staff_id"], booking["student_id"]), (1, 1))
        self.assertEqual(booking["appointment_date"], self.future.isoformat())
        self.assertEqual((booking["start_minute"], booking["end_minute"]), (540, 570))

    def test_confirmation_prg_and_refresh_do_not_book_again(self):
        self.add_hours()
        response = self.book()
        self.assertEqual(response.status_code, 303)
        self.assertIn("/student?date=", response.headers["Location"])
        page = self.client.get(response.headers["Location"])
        self.assertIn(b"Appointment booked successfully.", page.data)
        self.assertIn(f"Date: {self.future.isoformat()}".encode(), page.data)
        self.assertIn("Time: 09:00–09:30".encode(), page.data)
        self.assertNotIn(b"Booking reference", page.data)
        self.assertNotIn("09:00", PageElements(page.get_data(as_text=True)).radios)
        refreshed = self.client.get(response.headers["Location"])
        self.assertEqual(refreshed.status_code, 200)
        self.assertNotIn(b"Appointment booked successfully.", refreshed.data)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 1)

    def test_booked_time_removed_from_available_choices(self):
        self.add_hours()
        self.book("10:00")
        expected = ["09:00", "09:30", "10:30", "11:00", "11:30"]
        self.assertEqual(self.slot_times(), expected)
        self.assertEqual(PageElements(self.student_html()).radios, expected)

    def test_reject_second_booking_from_stale_tab(self):
        self.add_hours()
        self.assertIn("09:00", PageElements(self.student_html()).radios)
        self.book()
        response = self.book(reason="A second tab")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"no longer available", response.data)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 1)
        self.assertEqual(self.query("SELECT reason FROM appointments")[0]["reason"], "Discuss course selection")

    def test_simultaneous_booking_submissions_reserve_only_once(self):
        self.add_hours()
        barrier = threading.Barrier(2)

        def submit(reason):
            with self.app.test_client() as client:
                barrier.wait(timeout=10)
                response = client.post("/student/book", data={
                    "date": self.future.isoformat(), "time": "09:00", "reason": reason,
                })
                return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ["First browser tab", "Second browser tab"]))
        self.assertEqual(sorted(results), [303, 400])
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 1)

    def test_reject_forged_blocked_slot(self):
        self.add_hours()
        self.add_block()
        response = self.book("10:00")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"no longer available", response.data)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 0)

    def test_recheck_schedule_after_student_loaded_slots(self):
        self.add_hours()
        self.assertIn("10:00", PageElements(self.student_html()).radios)
        self.add_block()
        self.assertEqual(self.book("10:00").status_code, 400)
        range_id = self.query("SELECT id FROM weekly_availability")[0]["id"]
        self.client.post(f"/staff/availability/{range_id}/delete")
        self.assertEqual(self.book("09:00").status_code, 400)

    def test_reject_forged_outside_availability_and_invalid_times(self):
        self.add_hours()
        for slot in ["08:30", "12:00", "09:15", "99:00", "9:00", "", "09:00:00"]:
            with self.subTest(slot=slot):
                self.assertEqual(self.book(slot).status_code, 400)
        other_day = self.future + timedelta(days=1)
        self.assertEqual(self.book(day=other_day).status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 0)

    def test_reject_empty_and_excessive_reasons_preserve_form(self):
        self.add_hours()
        for reason in ["", " \n\t ", "x" * 501]:
            with self.subTest(length=len(reason)):
                response = self.book(reason=reason)
                self.assertEqual(response.status_code, 400)
                self.assertIn(f'value="{self.future.isoformat()}"'.encode(), response.data)
                self.assertIn(b'value="09:00" checked', response.data)
                if len(reason) > 500:
                    self.assertIn(reason.encode(), response.data)
                    self.assertIn(b"500 characters or fewer", response.data)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 0)
        self.assertEqual(self.book(reason="x" * 500).status_code, 303)

    def test_reason_is_escaped_on_validation_failure(self):
        self.add_hours()
        reason = '<script>alert("demo")</script>' + "x" * 500
        page = self.book(reason=reason)
        self.assertEqual(page.status_code, 400)
        self.assertNotIn(b"<script>", page.data)
        self.assertIn(b"&lt;script&gt;", page.data)

    def test_reject_blocks_conflicting_with_booking(self):
        self.add_hours()
        self.book()
        for start, end in [("09:00", "09:30"), ("08:45", "09:15"), ("09:15", "10:00")]:
            with self.subTest(start=start, end=end):
                response = self.add_block(start, end)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"overlaps an existing booking", response.data)
        self.assertEqual(self.add_block(all_day=True).status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM blocked_periods")), 0)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 1)

    def test_block_touching_booking_endpoint_is_allowed(self):
        self.add_hours()
        self.book("09:00")
        self.assertEqual(self.add_block("08:30", "09:00").status_code, 303)
        self.assertEqual(self.add_block("09:30", "10:00").status_code, 303)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 1)

    def test_reject_edit_or_delete_that_invalidates_future_booking(self):
        self.add_hours()
        self.book()
        range_id = self.query("SELECT id FROM weekly_availability")[0]["id"]
        for weekday, start, end in [
            (self.future.weekday(), "09:30", "12:00"),
            ((self.future.weekday() + 1) % 7, "09:00", "12:00"),
        ]:
            with self.subTest(weekday=weekday, start=start):
                response = self.client.post(f"/staff/availability/{range_id}/edit", data={
                    "weekday": str(weekday), "start_time": start, "end_time": end,
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"leave a future booking", response.data)
        response = self.client.post(f"/staff/availability/{range_id}/delete")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"leave a future booking", response.data)
        entry = self.query("SELECT * FROM weekly_availability")[0]
        self.assertEqual((entry["weekday"], entry["start_minute"], entry["end_minute"]), (self.future.weekday(), 540, 720))
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 1)

    def test_allow_availability_change_that_keeps_booking_covered(self):
        self.add_hours()
        self.book()
        range_id = self.query("SELECT id FROM weekly_availability")[0]["id"]
        response = self.client.post(f"/staff/availability/{range_id}/edit", data={
            "weekday": str(self.future.weekday()), "start_time": "08:00", "end_time": "10:00",
        })
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.slot_times(), ["08:00", "08:30", "09:30"])
        self.add_hours("13:00", "14:00")
        spare_id = self.query("SELECT id FROM weekly_availability WHERE start_minute = ?", (780,))[0]["id"]
        self.assertEqual(self.client.post(f"/staff/availability/{spare_id}/delete").status_code, 303)

    def test_protect_recurring_availability_for_booking_in_following_week(self):
        self.add_hours()
        self.assertEqual(self.book(day=self.future + timedelta(days=7)).status_code, 303)
        range_id = self.query("SELECT id FROM weekly_availability")[0]["id"]
        self.assertEqual(self.client.post(f"/staff/availability/{range_id}/delete").status_code, 400)

    def test_available_date_links_respect_horizon_blocks_and_bookings(self):
        for weekday in range(7):
            self.add_hours("09:00", "10:00", weekday)
        expected = [(self.today + timedelta(days=offset)).isoformat() for offset in range(14)]
        self.assertEqual(PageElements(self.student_html()).date_links, expected)
        blocked_date = self.today + timedelta(days=1)
        self.add_block(day=blocked_date, all_day=True)
        self.book("09:00")
        self.book("09:30")
        expected.remove(blocked_date.isoformat())
        expected.remove(self.future.isoformat())
        self.assertEqual(PageElements(self.student_html()).date_links, expected)
        selected = self.client.get("/student", query_string={"date": expected[0]})
        self.assertIn(b'aria-current="date"', selected.data)
        self.assertEqual(PageElements(selected.get_data(as_text=True)).radios, ["09:00", "09:30"])
        beyond_horizon = self.today + timedelta(days=14)
        self.assertNotIn(beyond_horizon.isoformat(), expected)
        self.assertEqual(PageElements(self.student_html(beyond_horizon)).radios, ["09:00", "09:30"])

    def test_form_has_no_identity_fields_and_uses_reason_limit(self):
        self.add_hours()
        html = self.student_html()
        fields = PageElements(html).field_names
        self.assertEqual(set(fields), {"date", "time", "reason"})
        self.assertIn('maxlength="500"', html)
        self.assertEqual(html.count("Dr. Lina Example (fictional)"), 1)

    def test_missing_entries_are_handled(self):
        for value in ["999", "bad", "999999999999999999999999999999"]:
            with self.subTest(edit=value):
                self.assertEqual(self.client.get("/staff", query_string={"edit": value}).status_code, 404)
        self.assertEqual(self.client.post("/staff/availability/999/delete").status_code, 400)
        self.assertEqual(self.client.post("/staff/blocks/999/delete").status_code, 400)
        response = self.client.post("/staff/availability/999/edit", data={
            "weekday": "0", "start_time": "09:00", "end_time": "10:00",
        })
        self.assertEqual(response.status_code, 400)

    def test_get_cannot_mutate_saved_records(self):
        self.add_hours()
        self.add_block()
        range_id = self.query("SELECT id FROM weekly_availability")[0]["id"]
        block_id = self.query("SELECT id FROM blocked_periods")[0]["id"]
        for path in [f"/staff/availability/{range_id}/delete", f"/staff/blocks/{block_id}/delete",
                     "/staff/availability", "/staff/blocks", "/student/book"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(len(self.query("SELECT * FROM weekly_availability")), 1)
        self.assertEqual(len(self.query("SELECT * FROM blocked_periods")), 1)

    def test_database_uniqueness_foreign_keys_and_reason_constraints(self):
        self.add_hours()
        self.book()
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            sql = ("INSERT INTO appointments "
                   "(staff_id, student_id, appointment_date, start_minute, end_minute, reason) "
                   "VALUES (?, ?, ?, ?, ?, ?)")
            for values in [
                (1, 1, self.future.isoformat(), 540, 570, "Duplicate"),
                (9999, 1, self.future.isoformat(), 600, 630, "Missing staff"),
                (1, 9999, self.future.isoformat(), 600, 630, "Missing student"),
                (1, 1, self.future.isoformat(), 600, 630, ""),
                (1, 1, self.future.isoformat(), 600, 630, "x" * 501),
            ]:
                with self.subTest(values=values):
                    with self.assertRaises(sqlite3.IntegrityError):
                        with db:
                            db.execute(sql, values)
        self.assertEqual(len(self.query("SELECT * FROM appointments")), 1)

    def test_recreate_application_preserves_all_saved_records(self):
        self.add_hours()
        self.add_block()
        self.book()
        recreated = create_app(self.config)
        with recreated.app_context():
            db = get_db()
            self.assertEqual(len(db.execute("SELECT * FROM staff").fetchall()), 1)
            self.assertEqual(len(db.execute("SELECT * FROM students").fetchall()), 1)
            self.assertEqual(len(db.execute("SELECT * FROM weekly_availability").fetchall()), 1)
            self.assertEqual(len(db.execute("SELECT * FROM blocked_periods").fetchall()), 1)
            self.assertEqual(len(db.execute("SELECT * FROM appointments").fetchall()), 1)
            self.assertEqual([slot["time"] for slot in get_available_slots(db, self.future)], ["09:30", "11:00", "11:30"])
        self.assertEqual(Path(recreated.config["DATABASE"]), Path(self.database).resolve())
        self.assertIn(b"09:30", recreated.test_client().get("/student", query_string={"date": self.future.isoformat()}).data)


if __name__ == "__main__":
    unittest.main()
