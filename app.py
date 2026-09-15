import os
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "registro.db"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", secrets.token_hex(32)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)

CATEGORIES = ["Luce", "Gas", "Condominio", "Riscaldamento", "Altro"]


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('proprietario','inquilino'))
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
            due_date TEXT NOT NULL,
            paid INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1)),
            paid_date TEXT,
            notes TEXT NOT NULL DEFAULT '',
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(expenses)")}
    for name, definition in (
        ("attachment_filename", "TEXT"),
        ("attachment_original_name", "TEXT"),
        ("attachment_mime", "TEXT"),
    ):
        if name not in columns:
            try:
                connection.execute(f"ALTER TABLE expenses ADD COLUMN {name} {definition}")
            except sqlite3.OperationalError as error:
                if "duplicate column name" not in str(error).lower():
                    raise
    users = [
        (os.getenv("OWNER_USER", "proprietario"), os.getenv("OWNER_PASSWORD", "cambia-subito"), os.getenv("OWNER_NAME", "Proprietario"), "proprietario"),
        (os.getenv("TENANT_USER", "inquilino"), os.getenv("TENANT_PASSWORD", "cambia-subito"), os.getenv("TENANT_NAME", "Inquilino"), "inquilino"),
    ]
    for username, password, name, role in users:
        connection.execute(
            "INSERT OR IGNORE INTO users(username,password_hash,display_name,role) VALUES(?,?,?,?)",
            (username, generate_password_hash(password), name, role),
        )
    connection.commit()
    connection.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def require_csrf():
    if not secrets.compare_digest(session.get("csrf", ""), request.form.get("csrf", "")):
        abort(400)


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def load_user():
    g.user = None
    if session.get("user_id"):
        g.user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        require_csrf()
        user = db().execute("SELECT * FROM users WHERE username=?", (request.form.get("username", "").strip(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect(url_for("index"))
        flash("Nome utente o password non corretti.", "error")
    return render_template("login.html")


@app.post("/logout")
def logout():
    require_csrf()
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    selected = request.args.get("filter", "tutte")
    selected_category = request.args.get("category", "Tutte")
    if selected_category != "Tutte" and selected_category not in CATEGORIES:
        selected_category = "Tutte"
    rows = db().execute(
        "SELECT e.*, u.display_name creator FROM expenses e JOIN users u ON u.id=e.created_by ORDER BY e.due_date DESC, e.id DESC"
    ).fetchall()
    all_rows = [dict(r) for r in rows]
    current_year = str(date.today().year)
    available_years = sorted({r["due_date"][:4] for r in all_rows} | {current_year}, reverse=True)
    selected_year = request.args.get("year", current_year)
    if selected_year != "Tutti" and selected_year not in available_years:
        selected_year = current_year if current_year in available_years else (available_years[0] if available_years else current_year)
    year_rows = all_rows if selected_year == "Tutti" else [r for r in all_rows if r["due_date"][:4] == selected_year]
    today = date.today().isoformat()
    expenses = []
    for item in year_rows:
        item["status"] = "pagato" if item["paid"] else ("scaduto" if item["due_date"] < today else "da_pagare")
        status_matches = selected == "tutte" or item["status"] == selected
        category_matches = selected_category == "Tutte" or item["category"] == selected_category
        if status_matches and category_matches:
            expenses.append(item)
    summary_rows = (
        year_rows
        if selected_category == "Tutte"
        else [r for r in year_rows if r["category"] == selected_category]
    )
    total = sum(r["amount_cents"] for r in summary_rows)
    paid = sum(r["amount_cents"] for r in summary_rows if r["paid"])
    category_summary = []
    for category in ["Tutte", *CATEGORIES]:
        category_rows = year_rows if category == "Tutte" else [r for r in year_rows if r["category"] == category]
        category_summary.append({
            "name": category,
            "count": len(category_rows),
            "total": sum(r["amount_cents"] for r in category_rows),
        })
    return render_template(
        "index.html", expenses=expenses, total=total, paid=paid,
        remaining=total-paid, selected=selected,
        selected_category=selected_category, category_summary=category_summary,
        selected_year=selected_year, available_years=available_years,
    )


def parse_form():
    category = request.form.get("category", "")
    description = request.form.get("description", "").strip()
    due_date = request.form.get("due_date", "")
    notes = request.form.get("notes", "").strip()
    paid = 1 if request.form.get("paid") == "on" else 0
    try:
        amount_cents = int(round(float(request.form.get("amount", "0").replace(",", ".")) * 100))
        datetime.strptime(due_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ValueError("Controlla importo e data.")
    if category not in CATEGORIES or not description or amount_cents < 0:
        raise ValueError("Compila correttamente tutti i campi obbligatori.")
    paid_date = request.form.get("paid_date") or (date.today().isoformat() if paid else None)
    if not paid:
        paid_date = None
    return category, description, amount_cents, due_date, paid, paid_date, notes


def save_attachment(upload):
    if not upload or not upload.filename:
        return None
    original_name = secure_filename(upload.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in {".pdf", ".jpg", ".jpeg"}:
        raise ValueError("Sono ammessi soltanto file PDF, JPG e JPEG.")
    header = upload.stream.read(5)
    upload.stream.seek(0)
    is_pdf = extension == ".pdf" and header.startswith(b"%PDF-")
    is_jpeg = extension in {".jpg", ".jpeg"} and header[:3] == b"\xff\xd8\xff"
    if not (is_pdf or is_jpeg):
        raise ValueError("Il contenuto del file non corrisponde al formato indicato.")
    stored_name = f"{secrets.token_hex(16)}{extension}"
    upload.save(UPLOAD_DIR / stored_name)
    mime = "application/pdf" if is_pdf else "image/jpeg"
    return stored_name, original_name, mime


def remove_attachment(filename):
    if filename:
        try:
            (UPLOAD_DIR / filename).unlink()
        except FileNotFoundError:
            pass


@app.errorhandler(RequestEntityTooLarge)
def file_too_large(_error):
    flash("L’allegato supera il limite di 10 MB.", "error")
    return redirect(request.referrer or url_for("index"))


@app.route("/nuova", methods=["GET", "POST"])
@login_required
def new_expense():
    if request.method == "POST":
        require_csrf()
        try:
            values = parse_form()
            attachment_values = save_attachment(request.files.get("attachment")) or (None, None, None)
            now = datetime.utcnow().isoformat(timespec="seconds")
            db().execute(
                "INSERT INTO expenses(category,description,amount_cents,due_date,paid,paid_date,notes,created_by,created_at,updated_at,attachment_filename,attachment_original_name,attachment_mime) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*values, g.user["id"], now, now, *attachment_values),
            )
            db().commit()
            flash("Spesa aggiunta.", "success")
            return redirect(url_for("index"))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("form.html", expense=None, categories=CATEGORIES)


@app.route("/modifica/<int:expense_id>", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    expense = db().execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
    if not expense:
        abort(404)
    if request.method == "POST":
        require_csrf()
        try:
            values = parse_form()
            attachment_values = save_attachment(request.files.get("attachment"))
            remove_requested = request.form.get("remove_attachment") == "on"
            if not attachment_values:
                attachment_values = (None, None, None) if remove_requested else (
                    expense["attachment_filename"], expense["attachment_original_name"], expense["attachment_mime"]
                )
            db().execute(
                "UPDATE expenses SET category=?,description=?,amount_cents=?,due_date=?,paid=?,paid_date=?,notes=?,updated_at=?,attachment_filename=?,attachment_original_name=?,attachment_mime=? WHERE id=?",
                (*values, datetime.utcnow().isoformat(timespec="seconds"), *attachment_values, expense_id),
            )
            db().commit()
            if (request.files.get("attachment") and request.files["attachment"].filename or remove_requested) and expense["attachment_filename"]:
                remove_attachment(expense["attachment_filename"])
            flash("Spesa aggiornata.", "success")
            return redirect(url_for("index"))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("form.html", expense=expense, categories=CATEGORIES)


@app.post("/elimina/<int:expense_id>")
@login_required
def delete_expense(expense_id):
    require_csrf()
    expense = db().execute("SELECT attachment_filename FROM expenses WHERE id=?", (expense_id,)).fetchone()
    db().execute("DELETE FROM expenses WHERE id=?", (expense_id,))
    db().commit()
    if expense:
        remove_attachment(expense["attachment_filename"])
    flash("Spesa eliminata.", "success")
    return redirect(url_for("index"))


@app.get("/allegato/<int:expense_id>")
@login_required
def attachment(expense_id):
    expense = db().execute(
        "SELECT attachment_filename, attachment_original_name, attachment_mime FROM expenses WHERE id=?",
        (expense_id,),
    ).fetchone()
    if not expense or not expense["attachment_filename"]:
        abort(404)
    return send_from_directory(
        UPLOAD_DIR, expense["attachment_filename"], as_attachment=True,
        download_name=expense["attachment_original_name"], mimetype=expense["attachment_mime"],
    )


@app.template_filter("euro")
def euro(cents):
    return f"€ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
