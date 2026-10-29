import os, math, secrets, sqlite3, hmac
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook
from io import BytesIO

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "attendance.db")
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "replace-this-with-a-long-random-secret-before-deployment")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Set SESSION_COOKIE_SECURE=True when deploying behind HTTPS.
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('teacher','student')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius_m INTEGER NOT NULL CHECK(radius_m BETWEEN 25 AND 2000),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS enrollments (
            class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at TEXT NOT NULL,
            PRIMARY KEY (class_id, student_id)
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('Present','Absent')),
            distance_m REAL,
            marked_at TEXT NOT NULL,
            UNIQUE(class_id, student_id, attendance_date)
        );
        """)
init_db()

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]

app.jinja_env.globals["csrf_token"] = csrf_token

@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        saved = session.get("_csrf", "")
        if not sent or not saved or not hmac.compare_digest(sent, saved):
            abort(400, description="Security token missing or expired. Refresh the page and try again.")

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def role_required(role):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator

def distance_m(lat1, lon1, lat2, lon2):
    radius = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return 2 * radius * math.asin(min(1, math.sqrt(a)))

def valid_coord(value, low, high):
    try:
        x = float(value)
        return x if math.isfinite(x) and low <= x <= high else None
    except (TypeError, ValueError):
        return None

def make_code(con):
    for _ in range(20):
        code = secrets.token_hex(3).upper()
        if not con.execute("SELECT 1 FROM classes WHERE code=?", (code,)).fetchone():
            return code
    raise RuntimeError("Could not create a unique class code.")

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("teacher_dashboard" if session["role"] == "teacher" else "student_dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        if not name or len(name) > 100 or not email or len(email) > 254 or "@" not in email:
            flash("Enter a valid name and email address.", "danger")
        elif role not in ("teacher", "student"):
            flash("Choose Teacher or Student.", "danger")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
        else:
            try:
                with db() as con:
                    con.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                                (name, email, generate_password_hash(password), role, now_iso()))
                flash("Account created. You can now sign in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("An account with that email already exists.", "danger")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            csrf_token()
            return redirect(url_for("teacher_dashboard" if user["role"] == "teacher" else "student_dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have signed out.", "info")
    return redirect(url_for("index"))

@app.route("/teacher")
@role_required("teacher")
def teacher_dashboard():
    selected_date = request.args.get("date", date.today().isoformat())
    try:
        selected_date = date.fromisoformat(selected_date).isoformat()
    except ValueError:
        selected_date = date.today().isoformat()
    with db() as con:
        classes = con.execute("""
            SELECT c.*, (SELECT COUNT(*) FROM enrollments e WHERE e.class_id=c.id) AS student_count
            FROM classes c WHERE c.teacher_id=? ORDER BY c.id DESC
        """, (session["user_id"],)).fetchall()
        class_rows = []
        for cls in classes:
            rows = con.execute("""
                SELECT u.name, u.email, COALESCE(a.status,'Absent') AS status,
                       a.marked_at, a.distance_m
                FROM enrollments e JOIN users u ON u.id=e.student_id
                LEFT JOIN attendance a ON a.class_id=e.class_id AND a.student_id=e.student_id AND a.attendance_date=?
                WHERE e.class_id=? ORDER BY u.name
            """, (selected_date, cls["id"])).fetchall()
            class_rows.append({"class": cls, "rows": rows,
                               "present": sum(1 for r in rows if r["status"] == "Present")})
    return render_template("teacher_dashboard.html", classes=class_rows, selected_date=selected_date)

@app.route("/manage-users")
@role_required("teacher")
def manage_users():
    """List teacher and student accounts. Only teachers may access this page."""
    with db() as con:
        users = con.execute(
            "SELECT id, name, email, role, created_at FROM users ORDER BY role, name"
        ).fetchall()
    return render_template("manage_users.html", users=users)


@app.route("/manage-users/delete/<int:user_id>", methods=["POST"])
@role_required("teacher")
def delete_user(user_id):
    """Delete a teacher/student and all related data via SQLite cascades."""
    with db() as con:
        target = con.execute(
            "SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None:
            flash("Account not found.", "danger")
            return redirect(url_for("manage_users"))

        if target["role"] not in ("teacher", "student"):
            flash("Only teacher and student accounts can be deleted here.", "danger")
            return redirect(url_for("manage_users"))

        if target["id"] == session.get("user_id"):
            flash("You cannot delete your own account while signed in.", "danger")
            return redirect(url_for("manage_users"))

        # The schema's ON DELETE CASCADE constraints remove related classes,
        # enrollments, and attendance rows. The transaction commits on exit.
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))

    flash(
        f"Deleted {target['role']} account for {target['name']}, including related classes and attendance records.",
        "success",
    )
    return redirect(url_for("manage_users"))


@app.route("/classes/create", methods=["POST"])
@role_required("teacher")
def create_class():
    name = request.form.get("name", "").strip()
    lat = valid_coord(request.form.get("latitude"), -90, 90)
    lon = valid_coord(request.form.get("longitude"), -180, 180)
    try:
        radius = int(request.form.get("radius_m", "100"))
    except ValueError:
        radius = 0
    if not name or len(name) > 120:
        flash("Enter a class name (up to 120 characters).", "danger")
    elif lat is None or lon is None:
        flash("Capture your current location before creating the class.", "danger")
    elif not 25 <= radius <= 2000:
        flash("Radius must be between 25 and 2,000 metres.", "danger")
    else:
        with db() as con:
            code = make_code(con)
            con.execute("""INSERT INTO classes(teacher_id,name,code,latitude,longitude,radius_m,created_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (session["user_id"], name, code, lat, lon, radius, now_iso()))
        flash(f"Class created. Share joining code {code} with your students.", "success")
    return redirect(url_for("teacher_dashboard"))

@app.route("/student")
@role_required("student")
def student_dashboard():
    with db() as con:
        classes = con.execute("""
            SELECT c.*, u.name AS teacher_name FROM enrollments e
            JOIN classes c ON c.id=e.class_id JOIN users u ON u.id=c.teacher_id
            WHERE e.student_id=? ORDER BY c.id DESC
        """, (session["user_id"],)).fetchall()
        recent = con.execute("""
            SELECT a.attendance_date, a.status, a.marked_at, a.distance_m, c.name AS class_name
            FROM attendance a JOIN classes c ON c.id=a.class_id
            WHERE a.student_id=? ORDER BY a.attendance_date DESC, a.id DESC LIMIT 30
        """, (session["user_id"],)).fetchall()
    return render_template("student_dashboard.html", classes=classes, recent=recent)

@app.route("/classes/join", methods=["POST"])
@role_required("student")
def join_class():
    code = request.form.get("code", "").strip().upper()
    with db() as con:
        cls = con.execute("SELECT * FROM classes WHERE code=?", (code,)).fetchone()
        if not cls:
            flash("No class found for that code.", "danger")
        else:
            try:
                con.execute("INSERT INTO enrollments(class_id,student_id,joined_at) VALUES(?,?,?)",
                            (cls["id"], session["user_id"], now_iso()))
                flash(f"You joined {cls['name']}.", "success")
            except sqlite3.IntegrityError:
                flash("You have already joined this class.", "info")
    return redirect(url_for("student_dashboard"))

@app.route("/attendance/mark", methods=["POST"])
@role_required("student")
def mark_attendance():
    try:
        class_id = int(request.form.get("class_id", ""))
    except ValueError:
        abort(400)
    lat = valid_coord(request.form.get("latitude"), -90, 90)
    lon = valid_coord(request.form.get("longitude"), -180, 180)
    if lat is None or lon is None:
        flash("Location was not available. Allow location access and try again.", "danger")
        return redirect(url_for("student_dashboard"))
    today = date.today().isoformat()
    with db() as con:
        cls = con.execute("""
            SELECT c.* FROM classes c JOIN enrollments e ON e.class_id=c.id
            WHERE c.id=? AND e.student_id=?
        """, (class_id, session["user_id"])).fetchone()
        if not cls:
            abort(403)
        dist = distance_m(lat, lon, cls["latitude"], cls["longitude"])
        status = "Present" if dist <= cls["radius_m"] else "Absent"
        try:
            con.execute("""INSERT INTO attendance(class_id,student_id,attendance_date,status,distance_m,marked_at)
                           VALUES(?,?,?,?,?,?)""",
                        (class_id, session["user_id"], today, status, round(dist, 1), now_iso()))
            flash(f"{status}: measured distance {dist:.1f} m; allowed radius {cls['radius_m']} m.", "success" if status == "Present" else "warning")
        except sqlite3.IntegrityError:
            flash("Attendance has already been submitted for this class today.", "info")
    return redirect(url_for("student_dashboard"))

@app.route("/classes/<int:class_id>/export")
@role_required("teacher")
def export_attendance(class_id):
    selected_date = request.args.get("date", date.today().isoformat())
    try:
        selected_date = date.fromisoformat(selected_date).isoformat()
    except ValueError:
        selected_date = date.today().isoformat()
    with db() as con:
        cls = con.execute("SELECT * FROM classes WHERE id=? AND teacher_id=?",
                          (class_id, session["user_id"])).fetchone()
        if not cls:
            abort(404)
        rows = con.execute("""
            SELECT u.name, u.email, COALESCE(a.status,'Absent') AS status,
                   a.distance_m, a.marked_at
            FROM enrollments e JOIN users u ON u.id=e.student_id
            LEFT JOIN attendance a ON a.class_id=e.class_id AND a.student_id=e.student_id AND a.attendance_date=?
            WHERE e.class_id=? ORDER BY u.name
        """, (selected_date, class_id)).fetchall()
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["Class", cls["name"]])
    ws.append(["Date", selected_date])
    ws.append([])
    ws.append(["Student Name", "Email", "Status", "Distance (m)", "Marked At"])
    for row in rows:
        ws.append([row["name"], row["email"], row["status"], row["distance_m"], row["marked_at"]])
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:E{max(4, ws.max_row)}"
    for col, width in {"A": 26, "B": 34, "C": 14, "D": 18, "E": 30}.items():
        ws.column_dimensions[col].width = width
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in cls["name"])[:40]
    return send_file(output, as_attachment=True,
                     download_name=f"{safe_name}_attendance_{selected_date}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "Bad request")), 400
@app.errorhandler(403)
def forbidden(error):
    return render_template("error.html", code=403, message="You do not have permission to access this page."), 403
@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", code=404, message="The requested page or class was not found."), 404

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
