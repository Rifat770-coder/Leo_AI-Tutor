import json
import os
import sqlite3
from html import escape
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_STORAGE_DIR", str(Path(__file__).resolve().parent / ".data" / "crewai"))

import learning
import storage
import ui
from contracts import Preferences, RequestError, SessionData

st.set_page_config(page_title="Leo AI Tutor", page_icon="🌱", layout="wide")
ui.theme()
st.html(Path(__file__).with_name("sidebar.html"), unsafe_allow_javascript=True)
state = st.session_state


def go(page, *, identifier=None):
    if identifier is not None:
        state.active_session = identifier
    state.next_page = page
    st.rerun()


def try_action(work, *, message=None):
    try:
        work()
    except (RequestError, ValidationError) as error:
        st.error(str(error) if isinstance(error, RequestError) else "Check the selected settings and try again.")
        return
    except sqlite3.Error:
        st.error("Student records are temporarily unavailable. Please try again.")
        return
    if message:
        st.toast(message)
    st.rerun()


with st.sidebar:
    with st.container(key="sidebar_brand"):
        st.image(str(Path(__file__).with_name("Docs") / "Leo AI Tutor Logo.png"), width="stretch")

if "owner" not in state:
    with st.container(key="student_workspace"):
        st.header("Your student workspace")
        st.caption("Sign in to resume lessons and keep your quiz results private.")
        mode = st.radio("Account action", ("Sign in", "Create account"), horizontal=True)
        with st.form("account", border=False):
            name = st.text_input("Student name or ID", max_chars=60)
            password = st.text_input("Password", type="password", max_chars=128)
            if st.form_submit_button(mode, type="primary", width="stretch"):
                def login():
                    state.owner = storage.authenticate(name, password, register=mode == "Create account")
                    state.next_page = "Learning Session"
                try_action(login)
    st.stop()

owner = state.owner
try:
    name, preferences = storage.profile(owner)
    rows, attempts = storage.records(owner)
    active_id = state.get("active_session")
    if not active_id and rows and "active_session" not in state:
        active_id = next((row["id"] for row in rows if row["state"] != "COMPLETED"), rows[0]["id"])
        state.active_session = active_id
    row = learning.session(owner, active_id) if active_id else None
    data = SessionData.model_validate_json(row["data"]) if row else None
    events = storage.activity(owner, active_id) if active_id else []
except (sqlite3.Error, ValidationError):
    st.error("Student records are temporarily unavailable. Please reload the page and try again.")
    st.stop()
except RequestError as error:
    st.error(str(error))
    if st.button("Return to sign in"):
        state.clear()
        st.rerun()
    st.stop()
current_attempts = [a for a in attempts if a["session_id"] == active_id]

with st.sidebar:
    if "next_page" in state:
        state.navigation = state.pop("next_page")
    page = st.radio("Navigation", ui.PAGES, key="navigation", label_visibility="collapsed", width="stretch")
    topic = row["topic"] if row else "No session selected"
    status = "Completed" if row and row["state"] == "COMPLETED" else "In progress"
    with st.container(key="sidebar_session"):
        st.markdown(
            f'<div class="leo-sidebar-summary"><small>Current session</small>'
            f'<strong title="{escape(topic)}">{escape(topic)}</strong>'
            + (f'<span class="leo-session-status">{status}</span>' if row else "")
            + '</div>',
            unsafe_allow_html=True,
        )
        if row and page != "Learning Session" and st.button("Open", help="Open session", width="content"):
            go("Learning Session")
    with st.container(key="sidebar_profile"):
        st.markdown(f'<div class="leo-sidebar-profile" title="{escape(name)}">'
                    f'<span class="leo-avatar" aria-hidden="true">{escape(name[:1].upper())}</span>'
                    f'<div><strong>{escape(name)}</strong><small>Student</small></div></div>',
                    unsafe_allow_html=True)
        if st.button("Sign out", width="content"):
            state.clear()
            st.rerun()

if page == "Learning Session" and row:
    heading, tools = st.columns([4, 1], vertical_alignment="center")
    with heading:
        ui.header(page)
    with tools:
        with st.container(key="session_tools"):
            if st.button("Agent Team", width="stretch"):
                go("Agent Activity")
            if st.button("New topic", width="stretch"):
                state.active_session = None
                state.pop("next_topic", None)
                st.rerun()
else:
    ui.header(page)

if row and row["job"]:
    @st.fragment(run_every=1)
    def poll_activity():
        updated = learning.session(owner, active_id)
        if updated["revision"] != row["revision"] or not updated["job"]:
            st.rerun()
    poll_activity()


def open_session(identifier):
    go("Learning Session", identifier=identifier)


def start_lesson(topic, level, language):
    identifier = storage.create_session(owner, topic, level, language, preferences.questions)
    state.active_session = identifier
    learning.dispatch(owner, identifier, "start")


def act(action, **fields):
    try_action(lambda: learning.dispatch(owner, active_id, action, **fields))


def finish_button():
    if st.button("Finish session"):
        try_action(lambda: learning.finish(owner, active_id), message="Session saved")


if page == "Dashboard":
    def new_session(topic=""):
        state.update(active_session=None, next_topic=topic)
        go("Learning Session")

    with st.container(key="dashboard"):
        with st.container(key="dashboard_hero"):
            st.subheader(f"Welcome, {name}")
            if row:
                st.caption("Selected session" if row["state"] == "COMPLETED" else "Continue learning")
                st.markdown(f'<p class="leo-dashboard-topic">{escape(row["topic"])}</p>', unsafe_allow_html=True)
                st.caption(row["level"])
            else:
                st.caption("Choose a topic to start your next lesson.")
            with st.container(horizontal=True):
                if row:
                    label = "View session" if row["state"] == "COMPLETED" else "Resume session"
                    if st.button(label, type="primary", icon=":material/play_arrow:"):
                        open_session(row["id"])
                if st.button("New learning session", type="secondary" if row else "primary", icon=":material/add:"):
                    new_session()

        ui.dashboard_metrics(rows, attempts)
        latest = ui.latest_attempts(attempts)
        recent, review = st.columns([1.6, 1])
        with recent, st.container(key="dashboard_recent"):
            st.subheader("Recent learning activity")
            if not rows:
                st.caption("Your sessions will appear here.")
            for item in rows[:5]:
                status = item["state"].replace("_", " ").title()
                attempt = latest.get(item["id"])
                metadata = [item["level"], status]
                if attempt:
                    metadata.append(f'{ui.score(attempt):.0%} quiz score')
                with st.container(key=f'dashboard_activity_{item["id"]}'):
                    if st.button(item["topic"], key=f'dashboard_{item["id"]}', width="stretch",
                                 icon=":material/arrow_outward:"):
                        open_session(item["id"])
                    st.caption(" · ".join(metadata))
        with review, st.container(key="dashboard_review"):
            st.subheader("Concepts to review")
            concepts = ui.review_concepts(latest)
            if not attempts:
                st.caption("Complete a quiz to get topics to review.")
            elif not concepts:
                st.caption("No weak concepts in your latest attempts.")
            else:
                for concept in concepts[:5]:
                    if st.button(concept, key=f'dashboard_review_{concept}', width="stretch",
                                 icon=":material/replay:"):
                        new_session(concept)

elif page == "Agent Activity":
    ui.activity_page(events)

elif page == "Quiz & Feedback":
    ui.quiz_review(attempts)

elif page == "Study History":
    if not rows:
        st.info("Start a lesson to build your study history.")
    for item in rows:
        with st.container(border=True):
            saved = SessionData.model_validate_json(item["data"])
            st.subheader(item["topic"])
            st.caption(f'{item["level"]} · {item["created"][:10]} · {item["state"].replace("_", " ").title()}')
            if saved.guidance:
                result = saved.guidance
                st.write(f'Score: {result["score"]}/{result["total"]}')
                if result["weak_concepts"]:
                    st.write("Concepts to review: " + ", ".join(result["weak_concepts"]))
            if saved.summary:
                st.write(saved.summary)
            label = "Reopen session" if item["state"] == "COMPLETED" else "Resume session"
            if st.button(label, key=f'history_{item["id"]}'):
                open_session(item["id"])

elif page == "My Progress":
    ui.analytics(rows, attempts, detail=True)

elif page == "Settings":
    with st.container(key="settings_page"), st.form("preferences", border=False):
        lesson, quiz = st.columns([1.15, 1])
        with lesson, st.container(key="settings_lesson"):
            st.subheader("Lesson preferences")
            language = st.selectbox(
                "Preferred language", ui.LANGUAGES, index=ui.LANGUAGES.index(preferences.language)
            )
            level = st.selectbox(
                "Preferred difficulty", ui.LEVELS, index=ui.LEVELS.index(preferences.level)
            )
        with quiz, st.container(key="settings_quiz"):
            st.subheader("Quiz preferences")
            count = st.number_input("Questions per quiz", 2, 10, preferences.questions, step=1)
            st.caption("Choose 2–10 questions for each standard quiz.")

        with st.container(key="settings_actions"):
            if st.form_submit_button("Save preferences", type="primary", icon=":material/check:", width="stretch"):
                try_action(
                    lambda: storage.save_preferences(owner, Preferences(language=language, level=level, questions=count)),
                    message="Preferences saved",
                )

elif not row:
    with st.container(border=True):
        st.subheader("What would you like to learn?")
        with st.form("new_session"):
            topic = st.text_input("Topic", value=state.get("next_topic", ""), max_chars=200)
            level = st.selectbox("Difficulty", ui.LEVELS, index=ui.LEVELS.index(preferences.level))
            language = st.selectbox("Lesson language", ui.LANGUAGES, index=ui.LANGUAGES.index(preferences.language))
            if st.form_submit_button("Start lesson", type="primary"):
                try_action(lambda: start_lesson(topic, level, language))
else:
    main, side = st.columns([2.65, 1], gap="medium")
    with main:
        current = ui.steps(row, data)
        if data.error:
            st.error(data.error)
        stage = learning.effective_state(row, data)
        if row["job"]:
            running = next((event for event in events if event["status"] in {"Running", "Waiting"}), None)
            with st.container(border=True):
                st.subheader(running["task"] if running else "Preparing the next step")
                st.caption(f'{running["agent"]} · {running["status"]}' if running else "Waiting")
                st.markdown('<div class="leo-skeleton"></div>' * 3, unsafe_allow_html=True)
        if not row["job"]:
            if stage == "REQUEST":
                if data.clarification:
                    st.info(data.clarification)
                with st.form(f"request_{active_id}"):
                    topic = st.text_input("Topic", row["topic"], max_chars=200)
                    if st.form_submit_button("Start lesson", type="primary"):
                        act("start", topic=topic)
            elif stage == "PLANNED":
                if data.error:
                    if st.button("Retry lesson", type="primary"):
                        act("teach")
                else:
                    act("teach")
            elif stage == "LESSON_READY":
                ui.lesson_content(row, data)
                with st.container(border=True, key="lesson_actions"):
                    st.subheader("Your Turn")
                    primary, more, simple = st.columns(3)
                    if primary.button("Continue to Quiz", type="primary", width="stretch"):
                        act("quiz")
                    if more.button("Explain More", width="stretch"):
                        act("more")
                    if simple.button("Explain more simply", width="stretch"):
                        act("simpler")
                    with st.expander("Change Difficulty"):
                        difficulty = st.selectbox("Difficulty", ui.LEVELS, index=ui.LEVELS.index(row["level"]))
                        if st.button("Apply difficulty", disabled=difficulty == row["level"]):
                            act("difficulty", level=difficulty)
                    with st.expander("Ask a Question"):
                        with st.form(f'follow_up_{active_id}_{len(data.conversation)}'):
                            question = st.text_input("Question about the lesson", max_chars=1000)
                            if st.form_submit_button("Ask Explainer"):
                                act("question", question=question)
                    finish_button()
            elif stage == "QUIZ_READY":
                st.subheader("Quiz Master’s questions")
                st.caption("Explainer → Quiz Master · Questions based on your lesson")
                def save_choice(question_id, key):
                    learning.save_answer(owner, active_id, data.quiz_id, question_id, state[key])

                answers = {}
                with st.container(key=f"quiz_scroll_{data.quiz_id}", border=True):
                    for number, question in enumerate(data.quiz["questions"], 1):
                        question_id = question["id"]
                        key = f'answer_{data.quiz_id}_{question_id}'
                        saved = data.draft.get(question_id)
                        options = question["options"]
                        answers[question_id] = st.radio(
                            f'{number}. {question["question"]}', options,
                            index=options.index(saved) if saved in options else None,
                            key=key, on_change=save_choice, args=(question_id, key),
                        )
                answered = sum(value is not None for value in answers.values())
                st.progress(answered / len(answers), text=f'{answered} of {len(answers)} answered')
                if st.button("Submit answers", type="primary"):
                    act("submit", answers=answers, quiz_id=data.quiz_id)
                finish_button()
            elif stage in {"FEEDBACK_READY", "COMPLETED"}:
                if stage == "COMPLETED":
                    st.success("Session completed")
                    st.write(data.summary)
                    if data.lesson:
                        with st.expander("Review lesson"):
                            ui.lesson_content(row, data)
                if current_attempts:
                    ui.feedback(current_attempts[0])
                if stage == "FEEDBACK_READY":
                    if data.guidance["weak_concepts"] and data.rounds < learning.review_limit():
                        if st.button("Re-teach weak concepts", type="primary"):
                            act("review")
                    elif data.guidance["weak_concepts"]:
                        st.caption("You’ve reached the review-round limit.")
                    finish_button()
    with side, st.container(key="session_overview"):
        ui.live_panel(row, current, events)
