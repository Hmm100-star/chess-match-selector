from __future__ import annotations

import csv
import logging
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask import has_request_context
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from db import Base, database_url_warnings, engine, redacted_database_url, session_scope
from models import (
    Attendance,
    Classroom,
    HomeworkEntry,
    Match,
    Round,
    RoundAuditEvent,
    Student,
    Teacher,
)
from pairing_logic import normalize_weights
from services import create_match_records, generate_matches_for_students, recalculate_totals


BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_WIN_WEIGHT = 0.7
DEFAULT_HOMEWORK_WEIGHT = 0.3
DEFAULT_HOMEWORK_TOTAL_QUESTIONS = 10
DEFAULT_HOMEWORK_MISSING_POLICY = "zero"
DEFAULT_HOMEWORK_MISSING_PENALTY_WRONG_PCT = 100
ATTENDANCE_STATUSES = {"present", "absent", "excused", "late"}
HOMEWORK_MISSING_POLICIES = {"zero", "exclude", "penalty"}
INPUT_TEMPLATE_URL = (
    "https://docs.google.com/spreadsheets/d/1kJKOxY_5oYmAcgvMtz_e9llXeYifauULxCitCE9vAQM/edit?usp=sharing"
)
GITHUB_URL = "https://github.com/Hmm100-star/chess-match-selector"

_tables_initialized = False


def _ensure_schema_evolution() -> None:
    inspector = inspect(engine)
    table_columns = {
        table_name: {column["name"] for column in inspector.get_columns(table_name)}
        for table_name in inspector.get_table_names()
    }

    additions: Dict[str, Dict[str, str]] = {
        "classrooms": {
            "default_win_weight": "default_win_weight INTEGER NOT NULL DEFAULT 70",
            "default_homework_weight": "default_homework_weight INTEGER NOT NULL DEFAULT 30",
            "default_homework_total_questions": (
                "default_homework_total_questions INTEGER NOT NULL DEFAULT 10"
            ),
            "default_homework_missing_policy": (
                "default_homework_missing_policy VARCHAR(20) NOT NULL DEFAULT 'zero'"
            ),
            "default_homework_missing_penalty_wrong_pct": (
                "default_homework_missing_penalty_wrong_pct INTEGER NOT NULL DEFAULT 100"
            ),
            "default_notation_required": "default_notation_required BOOLEAN NOT NULL DEFAULT TRUE",
            "fair_no_recent_rematch": "fair_no_recent_rematch BOOLEAN NOT NULL DEFAULT TRUE",
            "fair_recent_rematch_window": "fair_recent_rematch_window INTEGER NOT NULL DEFAULT 2",
            "fair_rotate_byes": "fair_rotate_byes BOOLEAN NOT NULL DEFAULT TRUE",
        },
        "students": {
            "homework_score_sum": "homework_score_sum FLOAT NOT NULL DEFAULT 0",
            "homework_score_count": "homework_score_count INTEGER NOT NULL DEFAULT 0",
        },
        "rounds": {
            "homework_total_questions": "homework_total_questions INTEGER NOT NULL DEFAULT 10",
            "homework_missing_policy": (
                "homework_missing_policy VARCHAR(20) NOT NULL DEFAULT 'zero'"
            ),
            "homework_missing_penalty_wrong_pct": (
                "homework_missing_penalty_wrong_pct INTEGER NOT NULL DEFAULT 100"
            ),
            "notation_required": "notation_required BOOLEAN NOT NULL DEFAULT TRUE",
            "finalized_at": "finalized_at DATETIME",
            "finalized_by_teacher_id": "finalized_by_teacher_id INTEGER",
        },
        "matches": {
            "white_notation_completed": (
                "white_notation_completed BOOLEAN NOT NULL DEFAULT FALSE"
            ),
            "black_notation_completed": (
                "black_notation_completed BOOLEAN NOT NULL DEFAULT FALSE"
            ),
        },
        "homework_entries": {
            "white_submitted": "white_submitted BOOLEAN NOT NULL DEFAULT FALSE",
            "black_submitted": "black_submitted BOOLEAN NOT NULL DEFAULT FALSE",
        },
    }

    with engine.begin() as connection:
        for table_name, column_definitions in additions.items():
            if table_name not in table_columns:
                continue
            for column_name, definition in column_definitions.items():
                if column_name in table_columns[table_name]:
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {definition}"))

        connection.execute(text("UPDATE rounds SET status='draft' WHERE status='open'"))
        connection.execute(text("UPDATE rounds SET status='finalized' WHERE status='completed'"))


def initialize_database() -> None:
    """Best-effort table initialization.

    In cloud deployments, database networking may be temporarily unavailable during
    process startup. Deferring table creation prevents the WSGI import step from
    crashing before the app can bind to a port.
    """

    global _tables_initialized
    if _tables_initialized:
        return

    try:
        logger.info(
            "Initializing database tables",
            extra={"database_url": redacted_database_url()},
        )
        Base.metadata.create_all(bind=engine)
        _ensure_schema_evolution()
        _tables_initialized = True
        logger.info("Database initialization completed")
    except Exception:
        logger.exception(
            "Database initialization failed; will retry on next request",
            extra={"database_url": redacted_database_url()},
        )

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

logger = logging.getLogger("chess_match_selector")
if not logger.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

for warning in database_url_warnings():
    logger.warning(warning, extra={"database_url": redacted_database_url()})


@app.before_request
def ensure_database_initialized() -> None:
    initialize_database()


def allowed_file(filename: str) -> bool:
    return filename.lower().endswith(".csv")


def generate_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf() -> None:
    token = session.get("csrf_token")
    form_token = request.form.get("csrf_token")
    if not token or not form_token or token != form_token:
        abort(400, description="Invalid CSRF token.")


def current_teacher_id() -> Optional[int]:
    return session.get("teacher_id")


def require_login() -> Teacher:
    teacher_id = current_teacher_id()
    if not teacher_id:
        return redirect(url_for("login"))
    with session_scope() as db:
        teacher = db.get(Teacher, teacher_id)
        if not teacher:
            session.pop("teacher_id", None)
            return redirect(url_for("login"))
        return teacher


def get_classroom_or_404(classroom_id: int, teacher_id: int) -> Classroom:
    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        if not classroom or classroom.teacher_id != teacher_id:
            abort(404)
        return classroom


def log_exception(error: Exception, support_id: str) -> None:
    extra = {"support_id": support_id}
    if has_request_context():
        extra.update(
            {
                "path": request.path,
                "method": request.method,
                "teacher_id": session.get("teacher_id"),
            }
        )
    logger.exception("Unhandled application error", extra=extra)


@app.context_processor
def inject_globals() -> Dict[str, str]:
    return {
        "csrf_token": generate_csrf_token(),
        "github_url": GITHUB_URL,
        "input_template_url": INPUT_TEMPLATE_URL,
    }


def require_request_csrf() -> None:
    token = session.get("csrf_token")
    supplied = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRF-Token")
        or (request.get_json(silent=True) or {}).get("csrf_token")
    )
    if not token or not supplied or supplied != token:
        abort(400, description="Invalid CSRF token.")


def parse_non_negative_int(value: str | None, label: str) -> int:
    if value is None or value == "":
        return 0
    parsed = int(float(value))
    if parsed < 0:
        raise ValueError(f"{label} cannot be negative.")
    return parsed


def parse_attendance_status(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in ATTENDANCE_STATUSES:
        return normalized
    return "present"


def parse_homework_policy(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in HOMEWORK_MISSING_POLICIES:
        return DEFAULT_HOMEWORK_MISSING_POLICY
    return normalized


def parse_bool_flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def log_round_event(
    db,
    round_record: Round,
    teacher: Teacher,
    action: str,
    details: str = "",
) -> None:
    db.add(
        RoundAuditEvent(
            round_id=round_record.id,
            teacher_id=teacher.id,
            action=action,
            details=details,
        )
    )


def build_history_maps(db, classroom_id: int) -> tuple[Dict[frozenset[int], int], Dict[int, int]]:
    rounds = (
        db.query(Round)
        .filter(Round.classroom_id == classroom_id)
        .order_by(Round.created_at.desc(), Round.id.desc())
        .all()
    )
    round_distance = {round_record.id: index + 1 for index, round_record in enumerate(rounds)}
    matches = (
        db.query(Match)
        .join(Round)
        .filter(Round.classroom_id == classroom_id)
        .all()
    )

    recent_opponents: Dict[frozenset[int], int] = {}
    bye_counts: Dict[int, int] = {}
    for match in matches:
        distance = round_distance.get(match.round_id, 9999)
        white_id = match.white_student_id
        black_id = match.black_student_id
        if white_id and black_id:
            key = frozenset({white_id, black_id})
            best = recent_opponents.get(key)
            if best is None or distance < best:
                recent_opponents[key] = distance
        if white_id and black_id is None:
            bye_counts[white_id] = bye_counts.get(white_id, 0) + 1
    return recent_opponents, bye_counts


def serialize_round_match(
    match: Match,
    student_map: Dict[int, Student],
    attendance_map: Dict[int, str],
    round_record: Round,
) -> Dict[str, object]:
    homework = match.homework_entry
    white = student_map.get(match.white_student_id) if match.white_student_id else None
    black = student_map.get(match.black_student_id) if match.black_student_id else None
    total = int(round_record.homework_total_questions or 0)

    white_correct = int(homework.white_correct) if homework else 0
    black_correct = int(homework.black_correct) if homework else 0

    if total > 0:
        white_incorrect = max(total - white_correct, 0)
        black_incorrect = max(total - black_correct, 0)
    else:
        white_incorrect = int(homework.white_incorrect) if homework else 0
        black_incorrect = int(homework.black_incorrect) if homework else 0

    return {
        "match": match,
        "white": white,
        "black": black,
        "white_attendance": attendance_map.get(match.white_student_id, "present")
        if match.white_student_id
        else "present",
        "black_attendance": attendance_map.get(match.black_student_id, "present")
        if match.black_student_id
        else "present",
        "white_correct": white_correct,
        "white_incorrect": white_incorrect,
        "black_correct": black_correct,
        "black_incorrect": black_incorrect,
        "white_submitted": bool(homework.white_submitted) if homework else False,
        "black_submitted": bool(homework.black_submitted) if homework else False,
        "white_notation_completed": bool(match.white_notation_completed),
        "black_notation_completed": bool(match.black_notation_completed),
    }


def apply_round_form_updates(
    db,
    round_record: Round,
    matches: Iterable[Match],
    attendance_records: Dict[int, Attendance],
    form,
) -> None:
    total_questions = parse_non_negative_int(
        form.get("homework_total_questions"), "Homework total questions"
    )
    if total_questions <= 0:
        raise ValueError("Homework total questions must be at least 1.")
    policy = parse_homework_policy(form.get("homework_missing_policy"))
    penalty_wrong_pct = parse_non_negative_int(
        form.get("homework_missing_penalty_wrong_pct"), "Missing homework penalty"
    )
    penalty_wrong_pct = max(0, min(100, penalty_wrong_pct))

    round_record.homework_total_questions = total_questions
    round_record.homework_missing_policy = policy
    round_record.homework_missing_penalty_wrong_pct = penalty_wrong_pct
    round_record.notation_required = parse_bool_flag(form.get("notation_required"))

    for student_id, attendance in attendance_records.items():
        attendance.status = parse_attendance_status(form.get(f"attendance_{student_id}"))

    for match in matches:
        result = (form.get(f"result_{match.id}", "") or "").strip().lower()
        if match.black_student_id is None:
            result = "bye"
        if result not in {"white", "black", "tie", "bye", ""}:
            raise ValueError("Invalid match result submitted.")

        homework_entry = match.homework_entry
        if not homework_entry:
            homework_entry = HomeworkEntry(match_id=match.id)
            db.add(homework_entry)
            match.homework_entry = homework_entry

        white_submitted = parse_bool_flag(form.get(f"white_submitted_{match.id}"))
        black_submitted = parse_bool_flag(form.get(f"black_submitted_{match.id}"))

        white_correct = parse_non_negative_int(
            form.get(f"white_correct_{match.id}"), "White homework correct"
        )
        black_correct = parse_non_negative_int(
            form.get(f"black_correct_{match.id}"), "Black homework correct"
        )

        if white_correct > total_questions or black_correct > total_questions:
            raise ValueError("Homework correct cannot exceed total homework questions.")

        homework_entry.white_submitted = white_submitted
        homework_entry.black_submitted = black_submitted
        homework_entry.white_correct = white_correct if white_submitted else 0
        homework_entry.black_correct = black_correct if black_submitted else 0
        homework_entry.white_incorrect = (
            total_questions - white_correct if white_submitted else 0
        )
        homework_entry.black_incorrect = (
            total_questions - black_correct if black_submitted else 0
        )

        match.result = result or None
        match.notes = (form.get(f"notes_{match.id}", "") or "").strip()
        match.white_notation_completed = parse_bool_flag(
            form.get(f"white_notation_{match.id}")
        )
        match.black_notation_completed = parse_bool_flag(
            form.get(f"black_notation_{match.id}")
        )
        match.updated_at = datetime.utcnow()


def round_completion_stats(round_record: Round, matches: Iterable[Match]) -> Dict[str, int]:
    total_matches = 0
    completed_results = 0
    notation_done = 0
    notation_total = 0
    homework_done = 0
    homework_total = 0

    for match in matches:
        total_matches += 1
        if match.black_student_id is None or match.result in {"white", "black", "tie", "bye"}:
            completed_results += 1

        if match.white_student_id:
            homework_total += 1
            notation_total += 1 if round_record.notation_required else 0
        if match.black_student_id:
            homework_total += 1
            notation_total += 1 if round_record.notation_required else 0

        entry = match.homework_entry
        if entry:
            if match.white_student_id and entry.white_submitted:
                homework_done += 1
            if match.black_student_id and entry.black_submitted:
                homework_done += 1
        if round_record.notation_required:
            if match.white_student_id and match.white_notation_completed:
                notation_done += 1
            if match.black_student_id and match.black_notation_completed:
                notation_done += 1

    return {
        "total_matches": total_matches,
        "completed_results": completed_results,
        "notation_done": notation_done,
        "notation_total": notation_total,
        "homework_done": homework_done,
        "homework_total": homework_total,
    }


def build_round_exceptions(round_record: Round, matches: Iterable[Match]) -> list[str]:
    exceptions: list[str] = []
    missing_results = 0
    missing_homework = 0
    missing_notation = 0
    for match in matches:
        if match.black_student_id and not (match.result or "").strip():
            missing_results += 1
        entry = match.homework_entry
        if match.white_student_id and not (entry and entry.white_submitted):
            missing_homework += 1
        if match.black_student_id and not (entry and entry.black_submitted):
            missing_homework += 1
        if round_record.notation_required:
            if match.white_student_id and not match.white_notation_completed:
                missing_notation += 1
            if match.black_student_id and not match.black_notation_completed:
                missing_notation += 1

    if missing_results:
        exceptions.append(f"{missing_results} match results missing")
    if missing_homework and round_record.homework_missing_policy == "exclude":
        exceptions.append(f"{missing_homework} homework submissions missing")
    if missing_notation:
        exceptions.append(f"{missing_notation} notation checks missing")
    return exceptions


@app.route("/")
def index() -> str:
    if current_teacher_id():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/setup", methods=["GET", "POST"])
def setup() -> str:
    with session_scope() as db:
        has_teacher = db.query(Teacher).count() > 0

    if has_teacher:
        return redirect(url_for("login"))

    error = None
    if request.method == "POST":
        require_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not password:
            error = "Username and password are required."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            with session_scope() as db:
                teacher = Teacher(
                    username=username,
                    password_hash=generate_password_hash(password),
                )
                db.add(teacher)
            return redirect(url_for("login"))

    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login() -> str:
    try:
        with session_scope() as db:
            has_teacher = db.query(Teacher).count() > 0
    except SQLAlchemyError:
        logger.exception(
            "Database query failed while loading login page",
            extra={"database_url": redacted_database_url(), "path": request.path},
        )
        raise

    if not has_teacher:
        return redirect(url_for("setup"))

    error = None
    if request.method == "POST":
        require_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        try:
            with session_scope() as db:
                teacher = db.query(Teacher).filter(Teacher.username == username).first()
        except SQLAlchemyError:
            logger.exception(
                "Database query failed during login submit",
                extra={"database_url": redacted_database_url(), "path": request.path},
            )
            raise

        if not teacher or not check_password_hash(teacher.password_hash, password):
            error = "Invalid username or password."
        else:
            session["teacher_id"] = teacher.id
            return redirect(url_for("dashboard"))

    return render_template("login.html", error=error)


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    if isinstance(error, HTTPException):
        return error
    support_id = uuid.uuid4().hex[:12]
    log_exception(error, support_id)
    return render_template("500.html", support_id=support_id), 500


@app.route("/health/db")
def health_db():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "database_url": redacted_database_url(),
            "warnings": database_url_warnings(),
        }
    except Exception as error:
        logger.exception(
            "Database health check failed",
            extra={"database_url": redacted_database_url()},
        )
        return {
            "status": "error",
            "database_url": redacted_database_url(),
            "warnings": database_url_warnings(),
            "error_type": type(error).__name__,
            "error": str(error),
        }, 500


@app.route("/logout", methods=["POST"])
def logout() -> str:
    require_csrf()
    session.pop("teacher_id", None)
    session.pop("classroom_id", None)
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard() -> str:
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    error = None
    with session_scope() as db:
        classrooms = (
            db.query(Classroom)
            .filter(Classroom.teacher_id == teacher.id)
            .order_by(Classroom.created_at.desc())
            .all()
        )

        if request.method == "POST":
            require_csrf()
            name = request.form.get("classroom_name", "").strip()
            if not name:
                error = "Class name is required."
            else:
                classroom = Classroom(name=name, teacher_id=teacher.id)
                db.add(classroom)
                db.commit()
                return redirect(url_for("dashboard"))

    return render_template("dashboard.html", teacher=teacher, classrooms=classrooms, error=error)


@app.route("/classrooms/<int:classroom_id>")
def classroom_overview(classroom_id: int) -> str:
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        if not classroom or classroom.teacher_id != teacher.id:
            abort(404)

        students = (
            db.query(Student)
            .filter(Student.classroom_id == classroom_id)
            .order_by(Student.name)
            .all()
        )
        rounds = (
            db.query(Round)
            .filter(Round.classroom_id == classroom_id)
            .order_by(Round.created_at.desc())
            .all()
        )

        attendance_summary = {
            student.id: {status: 0 for status in sorted(ATTENDANCE_STATUSES)}
            for student in students
        }
        round_attendance_counts: Dict[int, Dict[str, int]] = {
            round_record.id: {status: 0 for status in sorted(ATTENDANCE_STATUSES)}
            for round_record in rounds
        }
        attendance_records = (
            db.query(Attendance)
            .join(Round)
            .filter(Round.classroom_id == classroom_id)
            .all()
        )
        for record in attendance_records:
            if record.student_id in attendance_summary:
                attendance_summary[record.student_id][record.status] = (
                    attendance_summary[record.student_id].get(record.status, 0) + 1
                )
            if record.round_id in round_attendance_counts:
                round_attendance_counts[record.round_id][record.status] = (
                    round_attendance_counts[record.round_id].get(record.status, 0) + 1
                )

        round_ids = [round_record.id for round_record in rounds]
        matches_by_round: Dict[int, list[Match]] = {round_id: [] for round_id in round_ids}
        if round_ids:
            all_round_matches = (
                db.query(Match).filter(Match.round_id.in_(round_ids)).order_by(Match.id).all()
            )
            for match in all_round_matches:
                matches_by_round.setdefault(match.round_id, []).append(match)

        round_summaries = []
        exception_queue = []
        for round_record in rounds:
            round_matches = matches_by_round.get(round_record.id, [])
            completion = round_completion_stats(round_record, round_matches)
            exceptions = build_round_exceptions(round_record, round_matches)
            summary = {
                "round": round_record,
                "attendance": round_attendance_counts.get(
                    round_record.id, {status: 0 for status in sorted(ATTENDANCE_STATUSES)}
                ),
                "completion": completion,
                "exceptions": exceptions,
            }
            round_summaries.append(summary)
            if exceptions:
                exception_queue.append(summary)

    return render_template(
        "classroom.html",
        teacher=teacher,
        classroom=classroom,
        students=students,
        rounds=rounds,
        round_summaries=round_summaries,
        attendance_summary=attendance_summary,
        exception_queue=exception_queue,
        attendance_statuses=sorted(ATTENDANCE_STATUSES),
    )


@app.route("/classrooms/<int:classroom_id>/presets", methods=["POST"])
def update_classroom_presets(classroom_id: int) -> str:
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    require_csrf()
    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        if not classroom or classroom.teacher_id != teacher.id:
            abort(404)

        try:
            classroom.default_win_weight = parse_non_negative_int(
                request.form.get("default_win_weight"), "Default win weight"
            )
            classroom.default_homework_weight = parse_non_negative_int(
                request.form.get("default_homework_weight"), "Default homework weight"
            )
            classroom.default_homework_total_questions = max(
                1,
                parse_non_negative_int(
                    request.form.get("default_homework_total_questions"),
                    "Default homework total questions",
                ),
            )
            classroom.default_homework_missing_policy = parse_homework_policy(
                request.form.get("default_homework_missing_policy")
            )
            classroom.default_homework_missing_penalty_wrong_pct = max(
                0,
                min(
                    100,
                    parse_non_negative_int(
                        request.form.get("default_homework_missing_penalty_wrong_pct"),
                        "Default missing homework penalty",
                    ),
                ),
            )
            classroom.default_notation_required = parse_bool_flag(
                request.form.get("default_notation_required")
            )
            classroom.fair_no_recent_rematch = parse_bool_flag(
                request.form.get("fair_no_recent_rematch")
            )
            classroom.fair_recent_rematch_window = max(
                0,
                parse_non_negative_int(
                    request.form.get("fair_recent_rematch_window"),
                    "Recent rematch window",
                ),
            )
            classroom.fair_rotate_byes = parse_bool_flag(request.form.get("fair_rotate_byes"))
        except ValueError as exc:
            logger.warning(
                "Invalid classroom preset update",
                extra={"classroom_id": classroom_id, "error": str(exc)},
            )
            return redirect(url_for("classroom_overview", classroom_id=classroom_id))

    return redirect(url_for("classroom_overview", classroom_id=classroom_id))


@app.route("/classrooms/<int:classroom_id>/students", methods=["POST"])
def add_student(classroom_id: int) -> str:
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    require_csrf()
    name = request.form.get("student_name", "").strip()
    if not name:
        return redirect(url_for("classroom_overview", classroom_id=classroom_id))

    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        if not classroom or classroom.teacher_id != teacher.id:
            abort(404)
        db.add(Student(classroom_id=classroom_id, name=name))

    return redirect(url_for("classroom_overview", classroom_id=classroom_id))


@app.route("/students/<int:student_id>/update", methods=["POST"])
def update_student(student_id: int) -> str:
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    require_csrf()
    name = request.form.get("student_name", "").strip()
    notes = request.form.get("notes", "").strip()
    active = request.form.get("active") == "on"
    classroom_id = int(request.form.get("classroom_id", "0"))

    with session_scope() as db:
        student = db.get(Student, student_id)
        if not student:
            abort(404)
        classroom = db.get(Classroom, student.classroom_id)
        if not classroom or classroom.teacher_id != teacher.id:
            abort(404)

        if name:
            student.name = name
        student.notes = notes
        student.active = active

    return redirect(url_for("classroom_overview", classroom_id=classroom_id))


@app.route("/classrooms/<int:classroom_id>/rounds/new", methods=["GET", "POST"])
def new_round(classroom_id: int) -> str:
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        if not classroom or classroom.teacher_id != teacher.id:
            abort(404)

        students = (
            db.query(Student)
            .filter(Student.classroom_id == classroom_id, Student.active.is_(True))
            .order_by(Student.name)
            .all()
        )

        error = None
        if request.method == "POST":
            require_csrf()
            win_weight_raw = request.form.get("win_weight", "").strip()
            homework_weight_raw = request.form.get("homework_weight", "").strip()
            homework_total_raw = request.form.get("homework_total_questions", "").strip()
            homework_policy_raw = request.form.get("homework_missing_policy", "").strip()
            homework_penalty_raw = request.form.get(
                "homework_missing_penalty_wrong_pct", ""
            ).strip()
            notation_required = parse_bool_flag(request.form.get("notation_required"))
            fair_no_recent_rematch = parse_bool_flag(
                request.form.get("fair_no_recent_rematch")
            )
            fair_rotate_byes = parse_bool_flag(request.form.get("fair_rotate_byes"))
            fair_rematch_window_raw = request.form.get(
                "fair_recent_rematch_window", ""
            ).strip()

            try:
                default_win_weight = float((classroom.default_win_weight or 70) / 100.0)
                default_homework_weight = float(
                    (classroom.default_homework_weight or 30) / 100.0
                )
                win_weight = float(win_weight_raw) if win_weight_raw else default_win_weight
                homework_weight = (
                    float(homework_weight_raw)
                    if homework_weight_raw
                    else default_homework_weight
                )
                homework_total_questions = (
                    int(homework_total_raw)
                    if homework_total_raw
                    else int(
                        classroom.default_homework_total_questions
                        or DEFAULT_HOMEWORK_TOTAL_QUESTIONS
                    )
                )
                homework_missing_policy = parse_homework_policy(homework_policy_raw)
                homework_missing_penalty_wrong_pct = (
                    int(homework_penalty_raw)
                    if homework_penalty_raw
                    else int(
                        classroom.default_homework_missing_penalty_wrong_pct
                        or DEFAULT_HOMEWORK_MISSING_PENALTY_WRONG_PCT
                    )
                )
                fair_recent_rematch_window = (
                    int(fair_rematch_window_raw)
                    if fair_rematch_window_raw
                    else int(classroom.fair_recent_rematch_window or 2)
                )
            except ValueError:
                error = "Weights and round settings must be numeric."
            else:
                attendance_by_student = {}
                for student in students:
                    status = parse_attendance_status(request.form.get(f"attendance_{student.id}"))
                    attendance_by_student[student.id] = status
                # Keep support for legacy checkbox submissions.
                for value in request.form.getlist("absent_students"):
                    if value.isdigit():
                        attendance_by_student[int(value)] = "absent"
                present_students = [
                    student
                    for student in students
                    if attendance_by_student.get(student.id, "present") in {"present", "late"}
                ]

                if (
                    win_weight < 0
                    or homework_weight < 0
                    or homework_total_questions <= 0
                    or fair_recent_rematch_window < 0
                ):
                    error = "Weights must be zero or greater."
                elif homework_missing_penalty_wrong_pct < 0 or homework_missing_penalty_wrong_pct > 100:
                    error = "Missing homework penalty must be between 0 and 100."
                elif len(present_students) < 2:
                    error = "At least two present students are required to create matches."
                else:
                    try:
                        normalized_win, normalized_homework = normalize_weights(
                            win_weight, homework_weight
                        )
                        recent_opponents, bye_counts = build_history_maps(db, classroom_id)
                        matches, unpaired, df, id_order = generate_matches_for_students(
                            present_students,
                            normalized_win,
                            normalized_homework,
                            recent_opponents=recent_opponents,
                            bye_counts=bye_counts,
                            rematch_window=fair_recent_rematch_window,
                            avoid_recent_rematches=fair_no_recent_rematch,
                            rotate_byes=fair_rotate_byes,
                        )
                    except ValueError as exc:
                        error = str(exc)
                    else:
                        round_record = Round(
                            classroom_id=classroom_id,
                            win_weight=int(normalized_win * 100),
                            homework_weight=int(normalized_homework * 100),
                            homework_total_questions=homework_total_questions,
                            homework_missing_policy=homework_missing_policy,
                            homework_missing_penalty_wrong_pct=homework_missing_penalty_wrong_pct,
                            notation_required=notation_required,
                            status="draft",
                        )
                        db.add(round_record)
                        db.flush()

                        for student in students:
                            status = attendance_by_student.get(student.id, "present")
                            db.add(
                                Attendance(
                                    round_id=round_record.id,
                                    student_id=student.id,
                                    status=status,
                                )
                            )

                        match_records = create_match_records(
                            present_students, matches, unpaired, df, id_order
                        )
                        for record in match_records:
                            record.round_id = round_record.id
                            record.homework_entry = HomeworkEntry()
                            db.add(record)
                        log_round_event(
                            db,
                            round_record,
                            teacher,
                            action="round_created",
                            details=(
                                f"weights={round_record.win_weight}/{round_record.homework_weight}; "
                                f"homework_total={round_record.homework_total_questions}; "
                                f"policy={round_record.homework_missing_policy}"
                            ),
                        )

                        return redirect(
                            url_for(
                                "round_results",
                                classroom_id=classroom_id,
                                round_id=round_record.id,
                            )
                        )

    return render_template(
        "new_round.html",
        classroom=classroom,
        students=students,
        error=error,
        attendance_statuses=sorted(ATTENDANCE_STATUSES),
        default_win_weight=(classroom.default_win_weight or 70) / 100.0,
        default_homework_weight=(classroom.default_homework_weight or 30) / 100.0,
        default_homework_total_questions=(
            classroom.default_homework_total_questions or DEFAULT_HOMEWORK_TOTAL_QUESTIONS
        ),
        default_homework_missing_policy=(
            classroom.default_homework_missing_policy or DEFAULT_HOMEWORK_MISSING_POLICY
        ),
        default_homework_missing_penalty_wrong_pct=(
            classroom.default_homework_missing_penalty_wrong_pct
            or DEFAULT_HOMEWORK_MISSING_PENALTY_WRONG_PCT
        ),
        default_notation_required=bool(classroom.default_notation_required),
        default_fair_no_recent_rematch=bool(classroom.fair_no_recent_rematch),
        default_fair_recent_rematch_window=classroom.fair_recent_rematch_window or 2,
        default_fair_rotate_byes=bool(classroom.fair_rotate_byes),
    )


@app.route("/classrooms/<int:classroom_id>/rounds/<int:round_id>", methods=["GET", "POST"])
def round_results(classroom_id: int, round_id: int) -> str:
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher
    error: Optional[str] = None
    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        round_record = db.get(Round, round_id)
        if (
            not classroom
            or classroom.teacher_id != teacher.id
            or not round_record
            or round_record.classroom_id != classroom_id
        ):
            abort(404)

        matches = (
            db.query(Match)
            .filter(Match.round_id == round_id)
            .order_by(Match.id)
            .all()
        )
        attendance_records_list = (
            db.query(Attendance)
            .filter(Attendance.round_id == round_id)
            .all()
        )
        attendance_records = {
            record.student_id: record for record in attendance_records_list
        }

        students = (
            db.query(Student)
            .filter(Student.classroom_id == classroom_id)
            .all()
        )
        student_map = {student.id: student for student in students}
        attendance_map = {
            record.student_id: parse_attendance_status(record.status)
            for record in attendance_records_list
        }
        serialized_matches = [
            serialize_round_match(match, student_map, attendance_map, round_record)
            for match in matches
        ]

        if request.method == "POST":
            require_csrf()
            action = (request.form.get("action", "save") or "save").strip().lower()
            try:
                if action == "unlock":
                    reason = (request.form.get("unlock_reason", "") or "").strip()
                    if not reason:
                        raise ValueError("Unlock reason is required.")
                    round_record.status = "draft"
                    round_record.finalized_at = None
                    round_record.finalized_by_teacher_id = None
                    log_round_event(
                        db,
                        round_record,
                        teacher,
                        action="round_unlocked",
                        details=reason,
                    )
                else:
                    if round_record.status == "finalized":
                        raise ValueError("Round is finalized and must be unlocked before editing.")
                    apply_round_form_updates(
                        db=db,
                        round_record=round_record,
                        matches=matches,
                        attendance_records=attendance_records,
                        form=request.form,
                    )
                    if action == "finalize":
                        round_record.status = "finalized"
                        round_record.finalized_at = datetime.utcnow()
                        round_record.finalized_by_teacher_id = teacher.id
                        log_round_event(
                            db,
                            round_record,
                            teacher,
                            action="round_finalized",
                            details="Round finalized from results page.",
                        )
                    else:
                        round_record.status = "draft"
                        log_round_event(
                            db,
                            round_record,
                            teacher,
                            action="round_saved",
                            details="Manual save from results page.",
                        )

                all_matches = (
                    db.query(Match)
                    .join(Round)
                    .filter(Round.classroom_id == classroom_id)
                    .all()
                )
                recalculate_totals(students, all_matches)
                return redirect(
                    url_for("round_results", classroom_id=classroom_id, round_id=round_id)
                )
            except ValueError as exc:
                error = str(exc)

        audits = (
            db.query(RoundAuditEvent)
            .filter(RoundAuditEvent.round_id == round_id)
            .order_by(RoundAuditEvent.created_at.desc(), RoundAuditEvent.id.desc())
            .all()
        )
        attendance_map = {
            record.student_id: parse_attendance_status(record.status)
            for record in attendance_records_list
        }
        serialized_matches = [
            serialize_round_match(match, student_map, attendance_map, round_record)
            for match in matches
        ]
        completion = round_completion_stats(round_record, matches)
        exceptions = build_round_exceptions(round_record, matches)

    return render_template(
        "round_results.html",
        classroom=classroom,
        round_record=round_record,
        matches=matches,
        student_map=student_map,
        serialized_matches=serialized_matches,
        attendance_records=attendance_records_list,
        attendance_statuses=sorted(ATTENDANCE_STATUSES),
        completion=completion,
        exceptions=exceptions,
        audits=audits,
        error=error,
    )


@app.route(
    "/classrooms/<int:classroom_id>/rounds/<int:round_id>/autosave",
    methods=["POST"],
)
def autosave_round_results(classroom_id: int, round_id: int):
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    require_request_csrf()
    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        round_record = db.get(Round, round_id)
        if (
            not classroom
            or classroom.teacher_id != teacher.id
            or not round_record
            or round_record.classroom_id != classroom_id
        ):
            abort(404)

        if round_record.status == "finalized":
            return jsonify({"status": "locked", "error": "Round is finalized."}), 409

        matches = (
            db.query(Match)
            .filter(Match.round_id == round_id)
            .order_by(Match.id)
            .all()
        )
        attendance_records = {
            record.student_id: record
            for record in db.query(Attendance).filter(Attendance.round_id == round_id).all()
        }
        students = db.query(Student).filter(Student.classroom_id == classroom_id).all()

        try:
            apply_round_form_updates(
                db=db,
                round_record=round_record,
                matches=matches,
                attendance_records=attendance_records,
                form=request.form,
            )
            all_matches = (
                db.query(Match)
                .join(Round)
                .filter(Round.classroom_id == classroom_id)
                .all()
            )
            recalculate_totals(students, all_matches)
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400

    return jsonify({"status": "saved", "saved_at": datetime.utcnow().isoformat() + "Z"})


@app.route("/classrooms/<int:classroom_id>/export/students")
def export_students(classroom_id: int):
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        if not classroom or classroom.teacher_id != teacher.id:
            abort(404)

        students = (
            db.query(Student)
            .filter(Student.classroom_id == classroom_id)
            .order_by(Student.name)
            .all()
        )

    output_file = OUTPUTS_DIR / f"Student_Information_{classroom_id}.csv"
    with output_file.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            [
                "Student Name",
                "Total Wins",
                "Total Losses",
                "Total Ties",
                "# Times Played White",
                "# Times Played Black",
                "Correct Homework",
                "Incorrect Homework",
                "Homework Score %",
                "Homework % Wrong",
                "Homework Samples",
                "Notes",
            ]
        )
        for student in students:
            sample_count = int(student.homework_score_count or 0)
            avg_score = (
                float(student.homework_score_sum or 0.0) / sample_count
                if sample_count > 0
                else 0.0
            )
            writer.writerow(
                [
                    student.name,
                    student.total_wins,
                    student.total_losses,
                    student.total_ties,
                    student.times_white,
                    student.times_black,
                    student.homework_correct,
                    student.homework_incorrect,
                    round(avg_score * 100, 2),
                    round((1 - avg_score) * 100, 2) if sample_count > 0 else 100.0,
                    sample_count,
                    student.notes,
                ]
            )

    return send_file(output_file, as_attachment=True)


@app.route("/classrooms/<int:classroom_id>/rounds/<int:round_id>/export")
def export_round(classroom_id: int, round_id: int):
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    with session_scope() as db:
        round_record = db.get(Round, round_id)
        if not round_record or round_record.classroom_id != classroom_id:
            abort(404)
        classroom = db.get(Classroom, classroom_id)
        if not classroom or classroom.teacher_id != teacher.id:
            abort(404)

        matches = (
            db.query(Match)
            .filter(Match.round_id == round_id)
            .order_by(Match.id)
            .all()
        )
        students = (
            db.query(Student)
            .filter(Student.classroom_id == classroom_id)
            .all()
        )
        student_map = {student.id: student for student in students}
        attendance_records = (
            db.query(Attendance)
            .filter(Attendance.round_id == round_id)
            .all()
        )
        attendance_map = {
            record.student_id: parse_attendance_status(record.status)
            for record in attendance_records
        }
        serialized_matches = [
            serialize_round_match(match, student_map, attendance_map, round_record)
            for match in matches
        ]

    output_file = OUTPUTS_DIR / f"next_matches_{classroom_id}_{round_id}.csv"
    with output_file.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            [
                "White Player",
                "White Player Strength",
                "Black Player",
                "Black Player Strength",
                "Who Won",
                "White Homework Correct",
                "White Homework Incorrect",
                "Black Homework Correct",
                "Black Homework Incorrect",
                "Homework Total Questions",
                "Homework Missing Policy",
                "Missing Penalty Wrong %",
                "White Homework Submitted",
                "Black Homework Submitted",
                "White Attendance",
                "Black Attendance",
                "White Notation Completed",
                "Black Notation Completed",
                "Notes",
            ]
        )
        for row in serialized_matches:
            match = row["match"]
            writer.writerow(
                [
                    row["white"].name if row["white"] else "",
                    match.white_strength or "",
                    row["black"].name if row["black"] else "",
                    match.black_strength or "",
                    (match.result or "").title() if match.result else "",
                    row["white_correct"],
                    row["white_incorrect"],
                    row["black_correct"],
                    row["black_incorrect"],
                    round_record.homework_total_questions,
                    round_record.homework_missing_policy,
                    round_record.homework_missing_penalty_wrong_pct,
                    "Yes" if row["white_submitted"] else "No",
                    "Yes" if row["black_submitted"] else "No",
                    row["white_attendance"],
                    row["black_attendance"],
                    "Yes" if row["white_notation_completed"] else "No",
                    "Yes" if row["black_notation_completed"] else "No",
                    match.notes,
                ]
            )
        if len(serialized_matches) != len(matches):
            logger.warning(
                "Round export parity mismatch",
                extra={
                    "classroom_id": classroom_id,
                    "round_id": round_id,
                    "serialized_matches": len(serialized_matches),
                    "matches": len(matches),
                },
            )

    return send_file(output_file, as_attachment=True)


@app.route("/classrooms/<int:classroom_id>/import", methods=["GET", "POST"])
def import_students(classroom_id: int) -> str:
    teacher = require_login()
    if not isinstance(teacher, Teacher):
        return teacher

    error = None
    with session_scope() as db:
        classroom = db.get(Classroom, classroom_id)
        if not classroom or classroom.teacher_id != teacher.id:
            abort(404)

        if request.method == "POST":
            require_csrf()
            upload = request.files.get("file")
            replace_existing = request.form.get("replace_existing") == "on"

            if not upload or upload.filename == "":
                error = "Please upload a CSV file."
            else:
                filename = secure_filename(upload.filename)
                if not allowed_file(filename):
                    error = "Only .csv files are accepted."
                else:
                    path = UPLOADS_DIR / filename
                    upload.save(path)
                    df = pd.read_csv(path)
                    df.columns = df.columns.str.strip()
                    required = {
                        "Student Name",
                        "Total Wins",
                        "Total Losses",
                        "Total Ties",
                        "# Times Played White",
                        "# Times Played Black",
                        "Correct Homework",
                        "Incorrect Homework",
                        "Notes",
                    }
                    missing = required - set(df.columns)
                    if missing:
                        error = f"Missing columns: {', '.join(sorted(missing))}"
                    else:
                        def parse_int(value) -> int:
                            if pd.isna(value) or value == "":
                                return 0
                            parsed = int(float(value))
                            if parsed < 0:
                                raise ValueError("Numeric values cannot be negative.")
                            return parsed

                        students_to_add = []
                        try:
                            for _, row in df.iterrows():
                                name = str(row["Student Name"]).strip()
                                if not name:
                                    continue
                                homework_score_count = (
                                    parse_int(row["Homework Samples"])
                                    if "Homework Samples" in row
                                    else 0
                                )
                                homework_score_pct = (
                                    float(row["Homework Score %"]) / 100.0
                                    if "Homework Score %" in row
                                    and not pd.isna(row["Homework Score %"])
                                    else 0.0
                                )
                                students_to_add.append(
                                    Student(
                                        classroom_id=classroom_id,
                                        name=name,
                                        total_wins=parse_int(row["Total Wins"]),
                                        total_losses=parse_int(row["Total Losses"]),
                                        total_ties=parse_int(row["Total Ties"]),
                                        times_white=parse_int(row["# Times Played White"]),
                                        times_black=parse_int(row["# Times Played Black"]),
                                        homework_correct=parse_int(row["Correct Homework"]),
                                        homework_incorrect=parse_int(row["Incorrect Homework"]),
                                        homework_score_sum=(
                                            homework_score_pct * homework_score_count
                                        ),
                                        homework_score_count=homework_score_count,
                                        notes=str(row["Notes"]) if not pd.isna(row["Notes"]) else "",
                                        active=True,
                                    )
                                )
                        except ValueError as exc:
                            error = str(exc)
                        else:
                            if replace_existing:
                                db.query(Student).filter(
                                    Student.classroom_id == classroom_id
                                ).delete()
                            db.add_all(students_to_add)
                            return redirect(
                                url_for("classroom_overview", classroom_id=classroom_id)
                            )

    return render_template("import_students.html", classroom=classroom, error=error)


@app.route("/about")
def about_page() -> str:
    return render_template("about.html")


if __name__ == "__main__":
    debug_enabled = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", debug=debug_enabled)
