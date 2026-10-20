"""Local classroom demo: Flask forms and a small SQLite database."""

from datetime import date, datetime, timedelta
from pathlib import Path
import re
import secrets
import sqlite3
from zoneinfo import ZoneInfo

from flask import Flask, current_app, flash, g, redirect, render_template, request, session, url_for


PROJECT_DIR = Path(__file__).resolve().parent
RIYADH = ZoneInfo("Asia/Riyadh")
STAFF_ID = 1
STUDENT_ID = 1
SLOT_MINUTES = 30
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def now_riyadh():
    return datetime.now(RIYADH)


def format_time(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_date(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value or ""):
        raise ValueError("Choose a valid date in YYYY-MM-DD format.")
    try:
        selected_date = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Choose a valid calendar date.") from None
    if selected_date < now_riyadh().date():
        raise ValueError("Choose today or a future date; past dates are not allowed.")
    return selected_date


def parse_time(value, label, half_hour=True):
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value or ""):
        raise ValueError(f"Enter a valid {label} in HH:MM format.")
    hours, minutes = map(int, value.split(":"))
    if hours > 23 or minutes > 59:
        raise ValueError(f"Enter a valid {label} between 00:00 and 23:59.")
    total = hours * 60 + minutes
    if half_hour and total % SLOT_MINUTES:
        raise ValueError(f"The {label} must use a 30-minute boundary, such as 09:00 or 09:30.")
    return total


def parse_time_range(values, half_hour=True):
    start = parse_time(values.get("start_time"), "start time", half_hour)
    end = parse_time(values.get("end_time"), "end time", half_hour)
    if end <= start:
        raise ValueError("End time must be after start time within the same day.")
    return start, end


def overlaps(start, end, other_start, other_end):
    # Half-open intervals: touching endpoints do not overlap.
    return start < other_end and other_start < end


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript((PROJECT_DIR / "schema.sql").read_text(encoding="utf-8"))
    with db:
        db.execute(
            "INSERT OR IGNORE INTO staff (id, name, service) VALUES (?, ?, ?)",
            (STAFF_ID, "Dr. Lina Example (fictional)", "Computer science academic advising"),
        )
        db.execute(
            "INSERT OR IGNORE INTO students (id, name) VALUES (?, ?)",
            (STUDENT_ID, "Sami Example (fictional student)"),
        )


def get_available_slots(db, selected_date, now=None):
    """The single source of truth for date links, slot display and booking."""
    now = now or now_riyadh()
    if selected_date < now.date():
        return []
    day_text = selected_date.isoformat()
    ranges = db.execute(
        "SELECT start_minute, end_minute FROM weekly_availability "
        "WHERE staff_id = ? AND weekday = ? ORDER BY start_minute",
        (STAFF_ID, selected_date.weekday()),
    ).fetchall()
    blocks = db.execute(
        "SELECT start_minute, end_minute FROM blocked_periods WHERE staff_id = ? AND block_date = ?",
        (STAFF_ID, day_text),
    ).fetchall()
    bookings = db.execute(
        "SELECT start_minute, end_minute FROM appointments WHERE staff_id = ? AND appointment_date = ?",
        (STAFF_ID, day_text),
    ).fetchall()
    unavailable = list(blocks) + list(bookings)
    starts = set()
    for availability in ranges:
        for start in range(availability["start_minute"], availability["end_minute"], SLOT_MINUTES):
            end = start + SLOT_MINUTES
            if end > availability["end_minute"]:
                continue
            slot_start = datetime.combine(selected_date, datetime.min.time(), RIYADH) + timedelta(minutes=start)
            if slot_start <= now:
                continue
            if any(overlaps(start, end, item["start_minute"], item["end_minute"]) for item in unavailable):
                continue
            starts.add(start)
    return [
        {"time": format_time(start), "end": format_time(start + SLOT_MINUTES)}
        for start in sorted(starts)
    ]


def protect_future_bookings(db, resulting_ranges):
    """Reject an availability edit/delete if a future appointment loses coverage."""
    now = now_riyadh()
    bookings = db.execute(
        "SELECT appointment_date, start_minute, end_minute FROM appointments "
        "WHERE staff_id = ? AND appointment_date >= ?",
        (STAFF_ID, now.date().isoformat()),
    ).fetchall()
    for booking in bookings:
        booking_date = date.fromisoformat(booking["appointment_date"])
        booking_start = datetime.combine(booking_date, datetime.min.time(), RIYADH) + timedelta(
            minutes=booking["start_minute"]
        )
        if booking_start <= now:
            continue
        covered = any(
            item["weekday"] == booking_date.weekday()
            and item["start_minute"] <= booking["start_minute"]
            and booking["end_minute"] <= item["end_minute"]
            for item in resulting_ranges
        )
        if not covered:
            raise ValueError(
                f"This change would leave a future booking on {booking_date.isoformat()} "
                f"at {format_time(booking['start_minute'])} outside availability. "
                "Keep an availability range covering that appointment."
            )


def create_app(test_config=None):
    app = Flask(__name__, instance_path=str(PROJECT_DIR / "instance"))
    app.config.from_mapping(
        DATABASE=str(PROJECT_DIR / "instance" / "appointments.db"),
        SECRET_KEY=secrets.token_hex(32),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    database_path = Path(app.config["DATABASE"])
    if not database_path.is_absolute():
        database_path = PROJECT_DIR / database_path
    app.config["DATABASE"] = str(database_path.resolve())
    database_path.parent.mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)
    app.jinja_env.filters["clock"] = format_time
    with app.app_context():
        init_db()

    def staff_page(availability_values=None, block_values=None, availability_error=None,
                   block_error=None, edit_id=None):
        db = get_db()
        return render_template(
            "staff.html",
            weekdays=WEEKDAYS,
            availability=db.execute(
                "SELECT * FROM weekly_availability WHERE staff_id = ? ORDER BY weekday, start_minute",
                (STAFF_ID,),
            ).fetchall(),
            blocks=db.execute(
                "SELECT * FROM blocked_periods WHERE staff_id = ? ORDER BY block_date, start_minute",
                (STAFF_ID,),
            ).fetchall(),
            availability_values=availability_values or {},
            block_values=block_values or {},
            availability_error=availability_error,
            block_error=block_error,
            edit_id=edit_id,
            today=now_riyadh().date().isoformat(),
        )

    def student_page(date_value, reason="", selected_time="", error=None, confirmation=None):
        db = get_db()
        today = now_riyadh().date()
        slots = []
        try:
            selected_date = parse_date(date_value)
            slots = get_available_slots(db, selected_date)
        except ValueError as exc:
            error = error or str(exc)
        available_dates = []
        for offset in range(14):
            upcoming = today + timedelta(days=offset)
            if get_available_slots(db, upcoming):
                available_dates.append({"date": upcoming.isoformat(), "label": upcoming.strftime("%a, %d %b")})
        return render_template(
            "student.html",
            staff=db.execute("SELECT name, service FROM staff WHERE id = ?", (STAFF_ID,)).fetchone(),
            student=db.execute("SELECT name FROM students WHERE id = ?", (STUDENT_ID,)).fetchone(),
            today=today.isoformat(),
            date_value=date_value,
            available_dates=available_dates,
            slots=slots,
            reason=reason,
            selected_time=selected_time,
            error=error,
            confirmation=confirmation,
        )

    @app.get("/")
    def index():
        return redirect(url_for("student"))

    @app.get("/student")
    def student():
        return student_page(
            request.args.get("date", now_riyadh().date().isoformat()),
            confirmation=session.pop("booking_confirmation", None),
        )

    @app.post("/student/book")
    def book_appointment():
        date_value = request.form.get("date", "")
        time_value = request.form.get("time", "")
        reason_value = request.form.get("reason", "")
        reason = reason_value.strip()
        db = get_db()
        try:
            selected_date = parse_date(date_value)
            start = parse_time(time_value, "appointment time")
            if not reason:
                raise ValueError("Enter a reason for your appointment.")
            if len(reason) > 500:
                raise ValueError("The appointment reason must be 500 characters or fewer.")
            # A write lock covers the availability recheck AND insert. Other tabs wait.
            with db:
                db.execute("BEGIN IMMEDIATE")
                slots = get_available_slots(db, selected_date)
                if time_value not in {slot["time"] for slot in slots}:
                    raise ValueError("That time is no longer available. Choose an available time below.")
                db.execute(
                    "INSERT INTO appointments "
                    "(staff_id, student_id, appointment_date, start_minute, end_minute, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (STAFF_ID, STUDENT_ID, selected_date.isoformat(), start, start + SLOT_MINUTES, reason),
                )
        except ValueError as exc:
            return student_page(date_value, reason_value, time_value, str(exc)), 400
        except sqlite3.IntegrityError:
            return student_page(date_value, reason_value, time_value,
                                "That time could not be booked. Choose an available time and try again."), 409
        except sqlite3.OperationalError:
            app.logger.exception("Booking database operation failed")
            return student_page(date_value, reason_value, time_value,
                                "The database is busy. Please try booking again."), 503
        session["booking_confirmation"] = {
            "date": selected_date.isoformat(),
            "time": f"{format_time(start)}–{format_time(start + SLOT_MINUTES)}",
        }
        return redirect(url_for("student", date=selected_date.isoformat()), code=303)

    @app.get("/staff")
    def staff():
        edit_value = request.args.get("edit")
        if edit_value is None:
            return staff_page()
        if not re.fullmatch(r"[0-9]{1,9}", edit_value):
            return staff_page(availability_error="That availability entry was not found."), 404
        row = get_db().execute(
            "SELECT * FROM weekly_availability WHERE id = ? AND staff_id = ?",
            (int(edit_value), STAFF_ID),
        ).fetchone()
        if row is None:
            return staff_page(availability_error="That availability entry was not found."), 404
        values = {"weekday": str(row["weekday"]), "start_time": format_time(row["start_minute"]),
                  "end_time": format_time(row["end_minute"])}
        return staff_page(availability_values=values, edit_id=row["id"])

    @app.post("/staff/availability")
    @app.post("/staff/availability/<int(min=1,max=2147483647):range_id>/edit")
    def save_availability(range_id=None):
        values = request.form
        db = get_db()
        try:
            weekday_text = values.get("weekday", "")
            if weekday_text not in {str(day) for day in range(7)}:
                raise ValueError("Choose a valid day of the week.")
            weekday = int(weekday_text)
            start, end = parse_time_range(values)
            with db:
                db.execute("BEGIN IMMEDIATE")
                ranges = db.execute(
                    "SELECT * FROM weekly_availability WHERE staff_id = ?", (STAFF_ID,)
                ).fetchall()
                if range_id is not None and not any(item["id"] == range_id for item in ranges):
                    raise ValueError("That availability entry was not found. It may have been deleted.")
                remaining = [item for item in ranges if item["id"] != range_id]
                if any(item["weekday"] == weekday and overlaps(start, end, item["start_minute"], item["end_minute"])
                       for item in remaining):
                    raise ValueError("This range overlaps or duplicates saved availability for that day.")
                resulting = remaining + [{"weekday": weekday, "start_minute": start, "end_minute": end}]
                protect_future_bookings(db, resulting)
                if range_id is None:
                    db.execute(
                        "INSERT INTO weekly_availability (staff_id, weekday, start_minute, end_minute) "
                        "VALUES (?, ?, ?, ?)", (STAFF_ID, weekday, start, end),
                    )
                else:
                    db.execute(
                        "UPDATE weekly_availability SET weekday = ?, start_minute = ?, end_minute = ? "
                        "WHERE id = ? AND staff_id = ?", (weekday, start, end, range_id, STAFF_ID),
                    )
        except ValueError as exc:
            return staff_page(availability_values=values, availability_error=str(exc), edit_id=range_id), 400
        except sqlite3.Error:
            app.logger.exception("Availability database operation failed")
            return staff_page(availability_values=values, edit_id=range_id,
                              availability_error="The change could not be saved. Please try again."), 409
        flash("Availability updated." if range_id else "Availability added.", "weekly-success")
        return redirect(url_for("staff", _anchor="weekly"), code=303)

    @app.post("/staff/availability/<int(min=1,max=2147483647):range_id>/delete")
    def delete_availability(range_id):
        db = get_db()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                ranges = db.execute(
                    "SELECT * FROM weekly_availability WHERE staff_id = ?", (STAFF_ID,)
                ).fetchall()
                if not any(item["id"] == range_id for item in ranges):
                    raise ValueError("That availability entry was not found. It may have been deleted.")
                protect_future_bookings(db, [item for item in ranges if item["id"] != range_id])
                db.execute("DELETE FROM weekly_availability WHERE id = ? AND staff_id = ?", (range_id, STAFF_ID))
        except ValueError as exc:
            return staff_page(availability_error=str(exc)), 400
        except sqlite3.Error:
            app.logger.exception("Availability deletion failed")
            return staff_page(availability_error="The entry could not be deleted. Please try again."), 409
        flash("Availability deleted.", "weekly-success")
        return redirect(url_for("staff", _anchor="weekly"), code=303)

    @app.post("/staff/blocks")
    def add_block():
        values = request.form
        db = get_db()
        try:
            block_date = parse_date(values.get("date", "")).isoformat()
            all_day = values.get("all_day") == "1"
            start, end = (0, 1440) if all_day else parse_time_range(values, half_hour=False)
            with db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT start_minute, end_minute FROM blocked_periods WHERE staff_id = ? AND block_date = ?",
                    (STAFF_ID, block_date),
                ).fetchall()
                if any(overlaps(start, end, item["start_minute"], item["end_minute"]) for item in existing):
                    raise ValueError("This block overlaps or duplicates a saved block on that date.")
                bookings = db.execute(
                    "SELECT start_minute, end_minute FROM appointments WHERE staff_id = ? AND appointment_date = ?",
                    (STAFF_ID, block_date),
                ).fetchall()
                if any(overlaps(start, end, item["start_minute"], item["end_minute"]) for item in bookings):
                    raise ValueError("This block overlaps an existing booking. The booking has been kept.")
                db.execute(
                    "INSERT INTO blocked_periods (staff_id, block_date, start_minute, end_minute, all_day) "
                    "VALUES (?, ?, ?, ?, ?)", (STAFF_ID, block_date, start, end, int(all_day)),
                )
        except ValueError as exc:
            return staff_page(block_values=values, block_error=str(exc)), 400
        except sqlite3.Error:
            app.logger.exception("Block database operation failed")
            return staff_page(block_values=values, block_error="The block could not be saved. Please try again."), 409
        flash("Time blocked.", "block-success")
        return redirect(url_for("staff", _anchor="blocks"), code=303)

    @app.post("/staff/blocks/<int(min=1,max=2147483647):block_id>/delete")
    def delete_block(block_id):
        db = get_db()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute("DELETE FROM blocked_periods WHERE id = ? AND staff_id = ?", (block_id, STAFF_ID))
                if cursor.rowcount == 0:
                    raise ValueError("That blocked period was not found. It may already have been removed.")
        except ValueError as exc:
            return staff_page(block_error=str(exc)), 400
        except sqlite3.Error:
            app.logger.exception("Unblocking database operation failed")
            return staff_page(block_error="The block could not be removed. Please try again."), 409
        flash("Time unblocked.", "block-success")
        return redirect(url_for("staff", _anchor="blocks"), code=303)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
