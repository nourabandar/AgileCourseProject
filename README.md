# University Appointment Booking System

A local university appointment booking application built with Python, Flask and SQLite. It has two pages: **Staff** and **Student**.

## System functions

### 1. Manage weekly availability

Staff can add, edit and delete available hours for any day of the week. Multiple ranges are allowed on the same day, provided they do not overlap. Times use 30-minute boundaries. Changes that would leave a future appointment outside the saved availability are rejected.

### 2. Block dates and times

Staff can block a whole date or part of a day and remove a block using **Unblock**. Blocked times are removed from the available appointments. Overlapping blocks and blocks that conflict with an existing booking are rejected.

### 3. View available appointment times

Students can click an available date within the next 14 days or choose another date using the date picker. Changing the date automatically loads its available 30-minute times. Past, blocked and already booked times are excluded.

### 4. Book an appointment

Students select an available time, enter a reason of up to 500 characters and press **Book Appointment**. The system saves the appointment, displays a success message with its date and time, and removes that time from the available choices. Empty reasons and duplicate bookings are rejected.

## How to open the website on Windows

1. Install **Python 3.10 or newer** if needed. Make sure Python is available from Command Prompt.
2. Download this repository using **Code > Download ZIP**, or use the project ZIP shared with you.
3. Right-click the ZIP and choose **Extract All**.
4. Open the extracted folders until you can see `app.py` and `requirements.txt`.
5. Click File Explorer's address bar, type `cmd` and press **Enter**.
6. Run these commands in that Command Prompt:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

7. Open **http://127.0.0.1:5000** in your browser.

Internet access is needed to install dependencies the first time. After installation, the app runs offline. Keep Command Prompt open while using the website. Press **Ctrl+C** to stop the server.

To open it again later, open Command Prompt in the same project folder and run:

```bat
.venv\Scripts\python.exe app.py
```

## Using the system

Open **Staff** first and add weekly hours for the weekday of an upcoming appointment date. Add any blocked dates or times if needed. Then open **Student**, choose the date, select a time and enter an appointment reason.

The first launch has an empty schedule. The app automatically creates `instance/appointments.db` and the fictional identities **Dr. Lina Example** and **Sami Example**. Availability, blocks and appointments remain saved after restarting. Each computer has its own local database; records are not shared between computers.

Appointments last **30 minutes**, and all dates and times use **Asia/Riyadh (UTC+03:00)**. Availability stays within a single calendar day; overnight ranges are not supported. The `tzdata` dependency provides timezone support on Windows.

This is a classroom demo with **no login or authentication**. The Student and Staff links switch views and do not provide access control. Use fictional information only. The server runs on localhost by default; GitHub Pages cannot run this Flask backend.
