import os
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "registro.db"

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", secrets.token_hex(32)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
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
    today = date.today().isoformat()
    expenses = []
    for row in rows:
        item = dict(row)
        item["status"] = "pagato" if item["paid"] else ("scaduto" if item["due_date"] < today else "da_pagare")
        status_matches = selected == "tutte" or item["status"] == selected
        category_matches = selected_category == "Tutte" or item["category"] == selected_category
        if status_matches and category_matches:
            expenses.append(item)
    all_rows = [dict(r) for r in rows]
    total = sum(r["amount_cents"] for r in all_rows)
    paid = sum(r["amount_cents"] for r in all_rows if r["paid"])
    category_summary = []
    for category in ["Tutte", *CATEGORIES]:
        category_rows = all_rows if category == "Tutte" else [r for r in all_rows if r["category"] == category]
        category_summary.append({
            "name": category,
            "count": len(category_rows),
            "total": sum(r["amount_cents"] for r in category_rows),
        })
    return render_template(
        "index.html", expenses=expenses, total=total, paid=paid,
        remaining=total-paid, selected=selected,
        selected_category=selected_category, category_summary=category_summary,
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


@app.route("/nuova", methods=["GET", "POST"])
@login_required
def new_expense():
    if request.method == "POST":
        require_csrf()
        try:
            values = parse_form()
            now = datetime.utcnow().isoformat(timespec="seconds")
            db().execute(
                "INSERT INTO expenses(category,description,amount_cents,due_date,paid,paid_date,notes,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (*values, g.user["id"], now, now),
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
            db().execute(
                "UPDATE expenses SET category=?,description=?,amount_cents=?,due_date=?,paid=?,paid_date=?,notes=?,updated_at=? WHERE id=?",
                (*values, datetime.utcnow().isoformat(timespec="seconds"), expense_id),
            )
            db().commit()
            flash("Spesa aggiornata.", "success")
            return redirect(url_for("index"))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("form.html", expense=expense, categories=CATEGORIES)


@app.post("/elimina/<int:expense_id>")
@login_required
def delete_expense(expense_id):
    require_csrf()
    db().execute("DELETE FROM expenses WHERE id=?", (expense_id,))
    db().commit()
    flash("Spesa eliminata.", "success")
    return redirect(url_for("index"))


@app.template_filter("euro")
def euro(cents):
    return f"€ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
