-- Times are integer minutes after midnight; dates are local Asia/Riyadh dates.
-- CREATE IF NOT EXISTS and INSERT OR IGNORE in app.py preserve existing data.
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    service TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_availability (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_minute INTEGER NOT NULL CHECK (start_minute >= 0 AND start_minute % 30 = 0),
    end_minute INTEGER NOT NULL CHECK (end_minute <= 1440 AND end_minute % 30 = 0),
    CHECK (end_minute > start_minute),
    UNIQUE (staff_id, weekday, start_minute, end_minute)
);

CREATE TABLE IF NOT EXISTS blocked_periods (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    block_date TEXT NOT NULL CHECK (
        block_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
    ),
    start_minute INTEGER NOT NULL CHECK (start_minute >= 0),
    end_minute INTEGER NOT NULL CHECK (end_minute <= 1440),
    all_day INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1)),
    CHECK (end_minute > start_minute),
    CHECK (all_day = 0 OR (start_minute = 0 AND end_minute = 1440)),
    UNIQUE (staff_id, block_date, start_minute, end_minute)
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    appointment_date TEXT NOT NULL CHECK (
        appointment_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
    ),
    start_minute INTEGER NOT NULL CHECK (start_minute >= 0 AND start_minute % 30 = 0),
    end_minute INTEGER NOT NULL CHECK (end_minute <= 1440 AND end_minute = start_minute + 30),
    reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 500),
    UNIQUE (staff_id, appointment_date, start_minute)
);
