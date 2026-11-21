import hashlib
import os
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from contracts import Language, Level, Preferences, RequestError, SessionData

DATABASE = Path(os.getenv("LEO_DATABASE_PATH") or Path(__file__).resolve().parent / ".data" / "leo.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS student (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    salt BLOB NOT NULL, password BLOB NOT NULL, preferences TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES student(id),
    topic TEXT NOT NULL, level TEXT NOT NULL, state TEXT NOT NULL,
    created TEXT NOT NULL, updated TEXT NOT NULL, data TEXT NOT NULL,
    job TEXT, revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS session_owner ON session(student_id, updated DESC);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session(id),
    agent TEXT NOT NULL, task TEXT NOT NULL, status TEXT NOT NULL,
    started TEXT NOT NULL, ended TEXT, outcome TEXT, target TEXT
);
CREATE INDEX IF NOT EXISTS activity_session ON activity(session_id, id);
CREATE TABLE IF NOT EXISTS attempt (
    quiz_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session(id),
    difficulty TEXT NOT NULL, created TEXT NOT NULL,
    quiz TEXT NOT NULL, answers TEXT NOT NULL, result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempt_session ON attempt(session_id, created);
PRAGMA user_version = 1;
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection(*, write=False):
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            db.executescript(SCHEMA)
        if write:
            db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)


def authenticate(name: str, password: str, *, register: bool = False) -> str:
    name = name.strip()
    if not 1 <= len(name) <= 60 or not 8 <= len(password) <= 128:
        raise RequestError("Enter a name up to 60 characters and a password of 8 to 128 characters.")
    identity = name.casefold()
    with connection(write=register) as db:
        if register:
            salt = secrets.token_bytes(16)
            try:
                db.execute("INSERT INTO student VALUES (?, ?, ?, ?, ?)",
                           (identity, name, salt, password_hash(password, salt), Preferences().model_dump_json()))
            except sqlite3.IntegrityError as error:
                raise RequestError("This account could not be created. Sign in or choose another name.") from error
        else:
            row = db.execute("SELECT salt, password FROM student WHERE id=?", (identity,)).fetchone()
            digest = password_hash(password, row["salt"] if row else bytes(16))
            if not row or not hmac.compare_digest(digest, row["password"]):
                raise RequestError("The name or password is incorrect.")
    return identity


def profile(owner: str) -> tuple[str, Preferences]:
    with connection() as db:
        row = db.execute("SELECT name, preferences FROM student WHERE id=?", (owner,)).fetchone()
    if not row:
        raise RequestError("Sign in again to access your records.")
    return row["name"], Preferences.model_validate_json(row["preferences"])


def save_preferences(owner: str, preferences: Preferences) -> None:
    with connection(write=True) as db:
        db.execute("UPDATE student SET preferences=? WHERE id=?", (preferences.model_dump_json(), owner))


def create_session(owner: str, topic: str, level: Level, language: Language, count: int) -> str:
    topic = topic.strip()
    if not 1 <= len(topic) <= 200:
        raise RequestError("Enter a specific learning topic of up to 200 characters.")
    settings = Preferences(level=level, language=language, questions=count)
    session_id = uuid4().hex
    data = SessionData(language=settings.language, count=settings.questions)
    stamp = now()
    with connection(write=True) as db:
        db.execute(
            """INSERT INTO session (id, student_id, topic, level, state, created, updated, data)
               VALUES (?, ?, ?, ?, 'REQUEST', ?, ?, ?)""",
            (session_id, owner, topic, level, stamp, stamp, data.model_dump_json()),
        )
    return session_id


def owned(db, owner, session_id):
    row = db.execute("SELECT * FROM session WHERE id=? AND student_id=?", (session_id, owner)).fetchone()
    if not row:
        raise RequestError("This session is unavailable.")
    return dict(row)


def get_session(owner, session_id):
    with connection() as db:
        return owned(db, owner, session_id)


def records(owner):
    with connection() as db:
        sessions = db.execute(
            "SELECT * FROM session WHERE student_id=? ORDER BY updated DESC, rowid DESC", (owner,)
        ).fetchall()
        attempts = db.execute(
            """SELECT a.*, s.topic FROM attempt a JOIN session s ON s.id=a.session_id
               WHERE s.student_id=? ORDER BY a.created DESC, a.rowid DESC""", (owner,)
        ).fetchall()
    return [dict(row) for row in sessions], [dict(row) for row in attempts]


def activity(owner, session_id):
    with connection() as db:
        owned(db, owner, session_id)
        return [dict(row) for row in db.execute(
            "SELECT * FROM activity WHERE session_id=? ORDER BY id DESC", (session_id,)
        )]


def update(db, row, data, *, state, job=None, level=None, topic=None):
    cursor = db.execute(
        """UPDATE session SET state=?, job=?, level=?, topic=?, data=?, updated=?, revision=revision+1
           WHERE id=? AND student_id=? AND revision=?""",
        (state, job, level or row["level"], topic or row["topic"], data.model_dump_json(),
         now(), row["id"], row["student_id"], row["revision"]),
    )
    if cursor.rowcount != 1:
        raise RequestError("This session changed. Reload it and try again.")
