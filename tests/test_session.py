import json
import os
import unittest
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
CREWAI_STORAGE = TemporaryDirectory()
unittest.addModuleCleanup(CREWAI_STORAGE.cleanup)
os.environ.setdefault("CREWAI_STORAGE_DIR", CREWAI_STORAGE.name)

from crewai import BaseLLM
from pydantic import PrivateAttr
from streamlit.testing.v1 import AppTest

import learning
import storage
from contracts import RequestError, SessionData
from llm import ConfigurationError

ROOT = Path(__file__).resolve().parents[1]


class ScriptedLLM(BaseLLM):
    _steps = PrivateAttr(default_factory=deque)
    _problems = PrivateAttr(default_factory=list)

    def call(self, messages, **kwargs):
        if not self._steps:
            self._problems.append("Unexpected LLM call")
            raise RuntimeError(self._problems[-1])
        markers, response = self._steps.popleft()
        received = json.dumps(messages, ensure_ascii=False)
        for marker in markers:
            if marker not in received:
                self._problems.append(f"Missing {marker!r}")
        return response

    def final(self, markers, response):
        if isinstance(response, dict):
            response = json.dumps(response)
        self._steps.append((markers, "Thought: Ready.\nFinal Answer: " + response))

    def delegation(self, role, markers, response):
        payload = {"coworker": role, "task": f"Complete the {role} task.", "context": " ".join(markers)}
        action = "Thought: Delegate.\nAction: Delegate work to coworker\nAction Input: "
        self._steps.append((("Coordinator", *markers), action + json.dumps(payload)))
        self.final((role, *markers), response)
        self.final(("Coordinator",), response)


def loops_quiz(count):
    return {"topic": "Python Loops", "questions": [
        {"id": index, "question": f"Which keyword iterates over items ({index})?",
         "options": ["for", "try"], "correct_answer": "for",
         "explanation": "A for loop iterates over a sequence.", "concept": "Iteration"}
        for index in range(1, count + 1)
    ]}


def feedback(*correct):
    return {
        "feedback": [{"id": i, "correct": value, "feedback": "Use for to iterate over items."}
                     for i, value in enumerate(correct, 1)],
        "score": 0,  # The application must compute its own score.
        "total": len(correct), "weak_concepts": [],
        "suggestions": ["Trace the loop one item at a time."], "summary": "Practiced iteration.",
    }


class InlineWorker:
    def submit(self, work, *args):
        work(*args)


class SessionTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(storage, "DATABASE", Path(directory) / "test.sqlite3"))
        self.enterContext(patch.object(learning, "WORKERS", InlineWorker()))

    def app(self):
        return AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()

    def click(self, app, label, *, clean=True):
        next(button for button in app.button if button.label == label).click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        if clean:
            self.assertFalse(app.error, [error.value for error in app.error])

    def login(self, app, *, register=False, clean=True):
        if register:
            app.radio[0].set_value("Create account").run()
        app.text_input[0].set_value("Mina")
        app.text_input[1].set_value("student-secret")
        self.click(app, "Create account" if register else "Sign in", clean=clean)

    def test_full_session(self):
        llm = ScriptedLLM(model="scripted")
        llm.final(("Coordinator", "too broad"), {"clarification": "Which programming topic?"})
        llm.final(("Coordinator", "Python Loops"), {"topic": "Python Loops", "plan": "Teach iteration.",
                                                  "objectives": ["Trace a for loop"], "outline": ["Define iteration"]})
        llm.delegation("Explainer", ("Teach iteration.",), "Iterate over the items in a list.")
        llm.delegation("Explainer", ("Expand the previous", "Iterate over the items"), "Repeat for every item.")
        llm.delegation("Explainer", ("newly selected difficulty", "Intermediate"), "Iterate over the items again.")
        llm.delegation("Explainer", ("Rewrite the previous", "Iterate over the items"), "A loop repeats a step.")
        llm.delegation("Explainer", ("What does range do?", "A loop repeats"), "range produces a sequence of integers.")
        llm.delegation("Quiz Master", ("A loop repeats", "Trace a for loop"), loops_quiz(1))
        llm.delegation("Quiz Master", ("A loop repeats",), loops_quiz(3))
        llm.delegation("Evaluator", ("Which keyword iterates", "try"), feedback(False, True, True))
        llm.delegation("Explainer", ("Re-teach the weak concepts", "Iteration", "Use for to iterate"),
                       "Trace each item as it enters the loop.")
        llm.delegation("Quiz Master", ("Trace each item", "Iteration"), loops_quiz(2))
        llm.delegation("Evaluator", ("Which keyword iterates",), feedback(True, True))

        with patch("learning.create_llm", return_value=llm):
            app = self.app()
            self.login(app, register=True)
            self.click(app, "Start lesson", clean=False)
            self.assertIn("specific learning topic", app.error[0].value)
            app.text_input[0].set_value("too broad")
            self.click(app, "Start lesson")
            self.assertIn("Which programming topic?", app.info[0].value)
            app.text_input[0].set_value("Python Loops")
            self.click(app, "Start lesson")
            self.click(app, "Explain More")
            app.selectbox[0].set_value("Intermediate").run()
            self.click(app, "Apply difficulty")
            self.click(app, "Explain more simply")
            app.text_input[0].set_value("What does range do?")
            self.click(app, "Ask Explainer")
            self.assertTrue(any("sequence of integers" in item.value for item in app.markdown))
            self.click(app, "Continue to Quiz")
            self.assertFalse(any("Correct answer:" in item.value for item in app.markdown))
            self.click(app, "Submit answers", clean=False)
            self.assertIn("valid answer", app.error[0].value)
            app.radio[0].set_value("try").run()
            reloaded_quiz = self.app()
            self.login(reloaded_quiz)
            self.assertEqual(reloaded_quiz.radio[0].value, "try")
            for radio, choice in zip(app.radio[:3], ("try", "for", "for"), strict=True):
                radio.set_value(choice)
            self.click(app, "Submit answers")
            self.assertTrue(any("Score: 2/3" in item.value for item in app.markdown))
            owner = app.session_state.owner
            identifier = app.session_state.active_session
            first = storage.records(owner)[1][0]
            learning.dispatch(owner, identifier, "submit", quiz_id=first["quiz_id"])
            self.assertEqual(len(storage.records(owner)[1]), 1)
            app.sidebar.radio[0].set_value("Dashboard").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("67%" in item.value for item in app.markdown))
            self.assertTrue(any(button.label == "Iteration" for button in app.button))
            self.click(app, "Resume session")
            self.click(app, "Re-teach weak concepts")
            self.click(app, "Continue to Quiz")
            for radio in app.radio[:2]:
                radio.set_value("for")
            self.click(app, "Submit answers")
            self.assertTrue(any("Score: 2/2" in item.value for item in app.markdown))
            self.click(app, "Finish session")
            self.assertEqual(storage.get_session(owner, identifier)["state"], "COMPLETED")
            self.assertEqual(len(storage.records(owner)[1]), 2)

            app.sidebar.radio[0].set_value("Agent Activity").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Retry Quiz Master" in item.value for item in app.markdown))
            app.sidebar.radio[0].set_value("My Progress").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[1].value, "1")
            app.sidebar.radio[0].set_value("Quiz & Feedback").run()
            self.assertEqual(len(app.selectbox[0].options), 2)
            app.selectbox[0].set_value(1).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("67%" in item.value for item in app.markdown))
            app.sidebar.radio[0].set_value("Study History").run()
            self.click(app, "Reopen session")

            returning = self.app()
            self.login(returning)
            self.assertTrue(any("Session completed" in item.value for item in returning.success))
            self.assertEqual(returning.session_state.active_session, identifier)
            returning.sidebar.radio[0].set_value("Settings").run()
            returning.selectbox[0].set_value("Bangla")
            returning.selectbox[1].set_value("Advanced")
            returning.number_input[0].set_value(6)
            self.click(returning, "Save preferences")
            _, saved = storage.profile(owner)
            self.assertEqual((saved.language, saved.level, saved.questions), ("Bangla", "Advanced", 6))
        self.assertEqual(llm._problems, [])
        self.assertFalse(llm._steps)

    def test_failure_resume_and_isolation(self):
        owner = storage.authenticate("Mina", "student-secret", register=True)
        other = storage.authenticate("Other", "different-secret", register=True)
        with self.assertRaises(RequestError):
            storage.authenticate("Mina", "wrong-password")
        identifier = storage.create_session(owner, "Python Loops", "Beginner", "English", 3)
        with self.assertRaises(RequestError):
            storage.get_session(other, identifier)
        with self.assertRaises(RequestError):
            learning.dispatch(other, identifier, "start")
        with patch("learning.create_llm", side_effect=ConfigurationError("Provider unavailable")):
            learning.dispatch(owner, identifier, "start")
        row = learning.session(owner, identifier)
        self.assertEqual(row["state"], "FAILED")
        self.assertEqual(SessionData.model_validate_json(row["data"]).error, "Provider unavailable")
        self.assertEqual(storage.activity(owner, identifier)[0]["status"], "Failed")
        returning = self.app()
        self.login(returning, clean=False)
        self.assertEqual(returning.session_state.active_session, identifier)
        self.assertIn("Provider unavailable", returning.error[0].value)

    def test_refresh_while_job_is_queued(self):
        owner = storage.authenticate("Mina", "student-secret", register=True)
        identifier = storage.create_session(owner, "Python Loops", "Beginner", "English", 3)
        with patch.object(learning.WORKERS, "submit") as queued:
            learning.dispatch(owner, identifier, "start")
            with self.assertRaises(RequestError):
                learning.dispatch(owner, identifier, "start")
            app = self.app()
            self.login(app)
            self.assertIn("Coordinator · Waiting", [item.value for item in app.caption])
            work, *args = queued.call_args.args
            with patch("learning.create_llm", side_effect=ConfigurationError("Provider unavailable")):
                work(*args)
            app.run()
            self.assertIn("Provider unavailable", app.error[0].value)


if __name__ == "__main__":
    unittest.main()
