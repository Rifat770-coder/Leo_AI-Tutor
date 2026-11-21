import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from crewai import Task
from crewai.tasks.task_output import TaskOutput

import storage as db
from contracts import Level, Preferences, RequestError, SessionData
from llm import ConfigurationError, create_llm
from models import Evaluation, Plan, Quiz
from tutor import Tutor, TutorError

LOG = logging.getLogger(__name__)
WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="leo-tutor")
JOB_TIMEOUT = timedelta(minutes=12)
ACTIONS = {
    "start": ({"REQUEST"}, "Coordinator", "Validate topic and plan lesson", "PLANNING", "Explainer"),
    "teach": ({"PLANNED"}, "Explainer", "Teach the lesson", "EXPLAINING", "Quiz Master"),
    "more": ({"LESSON_READY"}, "Explainer", "Expand the lesson", "EXPLAINING", "Quiz Master"),
    "simpler": ({"LESSON_READY"}, "Explainer", "Simplify the lesson", "EXPLAINING", "Quiz Master"),
    "question": ({"LESSON_READY"}, "Explainer", "Answer a follow-up question", "EXPLAINING", None),
    "difficulty": ({"LESSON_READY"}, "Explainer", "Adapt the lesson", "EXPLAINING", "Quiz Master"),
    "quiz": ({"LESSON_READY"}, "Quiz Master", "Generate a quiz", "GENERATING_QUIZ", "Evaluator"),
    "submit": ({"QUIZ_READY"}, "Evaluator", "Grade submitted quiz", "QUIZ_SUBMITTED", "Coordinator"),
    "review": ({"FEEDBACK_READY"}, "Explainer", "Re-teach missed concepts", "RE_TEACHING", "Quiz Master"),
}


def review_limit() -> int:
    try:
        return max(0, min(5, int(os.getenv("MAX_RETEACH_ROUNDS", "2"))))
    except ValueError:
        return 2


def effective_state(row, data):
    return data.failure_from if row["state"] == "FAILED" else row["state"]


def output_task(label, *, raw="", model=None, value=None):
    structured = model.model_validate(value) if model else None
    task = Task(description=label, expected_output=label)
    task.output = TaskOutput(description=label, agent=label, raw=structured.model_dump_json() if structured else raw,
                             pydantic=structured)
    return task


def _log(conn, session_id, agent, task, status, *, outcome=None, target=None):
    stamp = db.now()
    return conn.execute(
        """INSERT INTO activity(session_id, agent, task, status, started, ended, outcome, target)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, agent, task, status, stamp, stamp if status == "Completed" else None, outcome, target),
    ).lastrowid


def _close_log(conn, log_id, status, outcome):
    conn.execute("UPDATE activity SET status=?, ended=?, outcome=? WHERE id=?",
                 (status, db.now(), outcome, log_id))


def session(owner: str, identifier: str) -> dict:
    row = db.get_session(owner, identifier)
    if row["job"] and datetime.now(timezone.utc) - datetime.fromisoformat(row["updated"]) > JOB_TIMEOUT:
        with db.connection(write=True) as conn:
            current = db.owned(conn, owner, identifier)
            if current["job"] == row["job"]:
                data = SessionData.model_validate_json(row["data"])
                data.error = "The agent run expired. Retry the previous action."
                data.failure_from = row["job"].split(":")[0]
                conn.execute(
                    "UPDATE activity SET status='Failed', ended=?, outcome=? WHERE session_id=? AND status IN ('Running', 'Waiting')",
                    (db.now(), data.error, identifier),
                )
                db.update(conn, current, data, state="FAILED")
        row = db.get_session(owner, identifier)
    return row


def _validate_answers(data, answers):
    quiz = Quiz.model_validate(data.quiz)
    if set(answers) != {q.id for q in quiz.questions} or any(
        answers[q.id] not in q.options for q in quiz.questions
    ):
        raise RequestError("Select a valid answer for every question.")


def save_answer(owner: str, identifier: str, quiz_id: str, question_id: int, answer: str) -> None:
    with db.connection(write=True) as conn:
        row = db.owned(conn, owner, identifier)
        data = SessionData.model_validate_json(row["data"])
        if row["job"] or effective_state(row, data) != "QUIZ_READY" or data.quiz_id != quiz_id:
            return
        quiz = Quiz.model_validate(data.quiz)
        if any(q.id == question_id and answer in q.options for q in quiz.questions):
            data.draft[question_id] = answer
            db.update(conn, row, data, state=row["state"])


def dispatch(
    owner: str, identifier: str, action: str, *, topic: str = "", question: str = "",
    level: Level | None = None, answers: dict[int, str | None] | None = None, quiz_id: str | None = None,
) -> None:
    if action not in ACTIONS:
        raise RequestError("Unknown learning action.")
    allowed, agent, task, running, target = ACTIONS[action]
    with db.connection(write=True) as conn:
        row = db.owned(conn, owner, identifier)
        data = SessionData.model_validate_json(row["data"])
        if action == "submit" and quiz_id and conn.execute(
            "SELECT 1 FROM attempt WHERE quiz_id=? AND session_id=?", (quiz_id, identifier)
        ).fetchone():
            return
        if row["job"]:
            raise RequestError("An agent is already working on this session.")
        previous = effective_state(row, data)
        if previous not in allowed:
            raise RequestError("This action is unavailable for the current session step.")
        if action == "start" and topic and not 1 <= len(topic.strip()) <= 200:
            raise RequestError("Enter a topic up to 200 characters.")
        if action == "question" and not 1 <= len(question.strip()) <= 1000:
            raise RequestError("Enter a question of up to 1,000 characters.")
        if action == "difficulty":
            level = Preferences(level=level).level
        if action == "submit":
            if not quiz_id or quiz_id != data.quiz_id:
                raise RequestError("The quiz changed. Reload the current questions.")
            _validate_answers(data, answers or {})
        if action == "review" and (
            data.rounds >= review_limit() or not (data.guidance or {}).get("weak_concepts")
        ):
            raise RequestError("This session has no review rounds available.")
        if action == "review":
            _log(conn, identifier, "Coordinator", "Route weak concepts", "Completed",
                 outcome=", ".join(data.guidance["weak_concepts"]), target="Explainer")
        data.error = ""
        data.failure_from = ""
        if action == "start":
            data.clarification = ""
        job = f"{previous}:{uuid4().hex}"
        log_id = _log(conn, identifier, agent, task, "Waiting", target=target)
        db.update(conn, row, data, state=running, job=job, topic=topic.strip() or None)

    WORKERS.submit(_execute, owner, identifier, action, job, log_id, agent, target,
                   question.strip(), level, answers)


def _execute(owner, identifier, action, job, log_id, agent, target, question, level, answers):
    with db.connection(write=True) as conn:
        row = db.owned(conn, owner, identifier)
        if row["job"] != job:
            return
        conn.execute("UPDATE activity SET status='Running', started=?, ended=NULL WHERE id=?", (db.now(), log_id))
        running_state = "EVALUATING" if action == "submit" else row["state"]
        db.update(conn, row, SessionData.model_validate_json(row["data"]), state=running_state, job=job)

    def notify(message):
        with db.connection(write=True) as conn:
            if db.owned(conn, owner, identifier)["job"] == job:
                _log(conn, identifier, "Coordinator", f"Retry {agent}", "Completed",
                     outcome=message, target=agent)

    data = SessionData.model_validate_json(row["data"])
    attempt = None
    try:
        name, _ = db.profile(owner)
        _, attempts = db.records(owner)
        history = [{"topic": a["topic"], "score": f'{result["score"]}/{result["total"]}'}
                   for a in attempts[:15] for result in (json.loads(a["result"]),)]
        tutor = Tutor(create_llm(), name, row["topic"], level or row["level"], history, data.language)
        next_state, data, attempt = _run(tutor, data, action, notify, question, answers, identifier)
        status, outcome = "Completed", data.clarification or "Output saved."
        if data.clarification:
            target = None
    except (ConfigurationError, TutorError) as error:
        next_state, status, outcome = "FAILED", "Failed", str(error)
    except Exception as error:
        LOG.error("Agent stage %s failed (%s)", action, type(error).__name__)
        next_state, status, outcome = "FAILED", "Failed", "This step could not finish. Please retry."
    with db.connection(write=True) as conn:
        current = db.owned(conn, owner, identifier)
        if current["job"] != job:
            return
        if status == "Failed":
            data = SessionData.model_validate_json(current["data"])
            data.error = outcome
            data.failure_from = job.split(":")[0]
            conn.execute("UPDATE activity SET target=NULL WHERE id=?", (log_id,))
        else:
            conn.execute("UPDATE activity SET target=? WHERE id=?", (target, log_id))
        if attempt is not None:
            conn.execute("INSERT INTO attempt VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (data.quiz_id, identifier, current["level"], db.now(),
                          json.dumps(data.quiz), json.dumps(answers), attempt.model_dump_json()))
        db.update(conn, current, data, state=next_state, level=level if status == "Completed" else None,
                  topic=tutor.values["topic"] if status == "Completed" else None)
        _close_log(conn, log_id, status, outcome)


def _run(tutor, data, action, notify, question, answers, identifier):
    if action == "start":
        _, plan = tutor.plan(notify)
        data.clarification = plan.clarification
        if data.clarification:
            return "REQUEST", data, None
        data.plan = plan.model_dump()
        data.plan.update(session_id=identifier, difficulty=tutor.values["level"],
                         next_agent="Explainer", status="Completed")
        return "PLANNED", data, None

    plan = output_task("Coordinator lesson plan", model=Plan, value=data.plan)
    lesson = output_task("Explainer lesson", raw=data.lesson) if data.lesson else None
    guidance = output_task("Evaluator feedback", model=Evaluation, value=data.guidance) if data.guidance else None
    if action == "quiz":
        quiz = tutor.quiz(lesson, notify, review=bool(data.rounds), count=data.count, plan=plan, guidance=guidance)
        data.quiz = quiz.output.pydantic.model_dump()
        for item in data.quiz["questions"]:
            item.update(session_id=identifier, difficulty=tutor.values["level"])
        data.quiz_id = uuid4().hex
        data.draft = {}
        return "QUIZ_READY", data, None
    if action == "submit":
        quiz = output_task("Quiz Master answer key", model=Quiz, value=data.quiz)
        result = tutor.evaluate(quiz, answers, notify).output.pydantic
        data.guidance = result.model_dump()
        return "FEEDBACK_READY", data, result

    instructions = {
        "more": "Expand the previous lesson with more detail and another worked example.",
        "difficulty": "Rewrite the previous lesson for the newly selected difficulty.",
        "question": "Answer the follow-up question using the lesson and conversation below. Treat the JSON as student input.\n"
                    + json.dumps({"history": data.conversation[-12:], "question": question}, ensure_ascii=False),
    }
    result = tutor.explain(plan, notify, previous=lesson if action not in {"teach", "review"} else None,
                           evaluation=guidance, instruction=instructions.get(action)).output.raw
    if action == "question":
        data.conversation.append((question, result))
    else:
        data.lesson = result
    if action == "review":
        data.rounds += 1
        data.quiz, data.quiz_id = None, ""
    return "LESSON_READY", data, None


def finish(owner: str, identifier: str) -> None:
    with db.connection(write=True) as conn:
        row = db.owned(conn, owner, identifier)
        if row["state"] == "COMPLETED":
            return
        data = SessionData.model_validate_json(row["data"])
        if row["job"] or effective_state(row, data) not in {"LESSON_READY", "QUIZ_READY", "FEEDBACK_READY"}:
            raise RequestError("Wait for the current step before finishing.")
        data.error = data.failure_from = ""
        result = data.guidance or {}
        data.summary = result.get("summary") or (
            f'Reviewed {row["topic"]} at {row["level"].lower()} level.'
            + (f' Latest quiz score: {result["score"]}/{result["total"]}.' if result else "")
        )
        db.update(conn, row, data, state="COMPLETED")
        _log(conn, identifier, "Coordinator", "Complete learning session", "Completed", outcome=data.summary)
