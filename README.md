# College Geofencing Attendance Management System

A Flask + SQLite college project with dark UI, teacher/student registration, GPS geofencing, class joining codes, date-filtered attendance and Excel export.

## Requirements
- Python 3.10 or newer
- VS Code (recommended)
- A modern browser with location permission enabled

## Windows / VS Code setup
1. Extract this folder and open it in VS Code.
2. Open **Terminal → New Terminal** in the project folder.
3. Create a virtual environment:
   ```powershell
   py -m venv .venv
   ```
4. Activate it:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks activation, use Command Prompt terminal and run:
   ```bat
   .venv\Scripts\activate.bat
   ```
5. Install packages:
   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
6. (Recommended) Set a secret key in the terminal before running:
   ```powershell
   $env:SECRET_KEY = "paste-a-long-random-secret-here"
   ```
   You can generate one with:
   ```powershell
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
7. Start the server:
   ```powershell
   python app.py
   ```
8. Open http://127.0.0.1:5000 in your browser. Geolocation works on localhost in modern browsers.
9. Register a Teacher account, sign in, create a class and capture location. Copy the generated code.
10. Register a Student account in another browser/incognito window, join using the code, and mark attendance.

## Features
- Teacher and student registration / sign-in
- Teacher-only Manage Users page to list and delete teacher/student accounts
- Deleting a user also deletes their related classes, enrollments and attendance records (permanent)
- Password hashes (passwords are never stored in plain text)
- Role-protected pages and CSRF tokens for form posts
- Teacher creates a class using current location and a configurable 25–2,000 m radius
- Random unique 6-character class code
- Students join a class with that code
- Attendance is recorded once per student/class/day
- Distance calculated on the server with the Haversine formula
- Present when inside radius; an attempted check-in outside radius is recorded as Absent
- Teacher dashboard date picker and per-class Excel export
- Student's joined classes and recent attendance history

## Notes / limitations
- The app uses the server's local date for attendance dates. Set the server timezone appropriately for your college.
- Browser GPS is not cryptographic proof of physical location and can be spoofed. For a production deployment, consider device integrity checks, campus Wi-Fi validation, audit logs and stricter check-in windows.
- Do not expose the development server directly to the internet. Use HTTPS, a production WSGI server, secure secret management, backups, rate limiting and account recovery for deployment.
- The selected date shows Present if a record exists with status Present; otherwise enrolled students appear as Absent for that date. This is useful for daily roll call, but if classes don't meet every day, export only dates on which the class was held.
- The database file `attendance.db` is created automatically on first run. Keep a backup; do not delete it unless you want to reset all data.
