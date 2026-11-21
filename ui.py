import json
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

import streamlit as st

import learning
import storage
from contracts import SessionData

PAGES = ("Dashboard", "Learning Session", "Agent Activity", "Quiz & Feedback", "Study History", "My Progress", "Settings")
LEVELS = ("Beginner", "Intermediate", "Advanced")
LANGUAGES = ("English", "Bangla")
AGENTS = ("Coordinator", "Explainer", "Quiz Master", "Evaluator")
CAPTIONS = {
    "Dashboard": "Your learning activity and next steps.",
    "Learning Session": "Learn, practice, and get feedback with Leo's multi-agent team.",
    "Agent Activity": "Execution records and handoffs from your learning session.",
    "Quiz & Feedback": "Review submitted answers, scores, and explanations.",
    "Study History": "Open a previous lesson or resume an unfinished session.",
    "My Progress": "Track completed sessions and quiz performance by topic.",
    "Settings": "Choose the defaults for new learning sessions.",
}
AGENT_ICONS = ("⌘", "▤", "☷", "▥")


def theme():
    st.markdown(Path(__file__).with_name("theme.html").read_text(encoding="utf-8-sig"), unsafe_allow_html=True)


def header(page):
    st.markdown(
        f'<div class="leo-heading"><span class="leo-mark" aria-hidden="true">▤</span>'
        f'<div><h1>{page}</h1><p>{CAPTIONS[page]}</p></div></div>', unsafe_allow_html=True
    )


def steps(row, data):
    stage = learning.effective_state(row, data)
    current = 1
    if data.plan:
        current = 2
    if stage in {"GENERATING_QUIZ", "QUIZ_READY", "QUIZ_SUBMITTED", "EVALUATING"}:
        current = 3
    if stage in {"FEEDBACK_READY", "COMPLETED"}:
        current = 4
    parts = []
    for index, item in enumerate(("Request", "Explain", "Quiz", "Feedback"), 1):
        if index > 1:
            parts.append('<i aria-hidden="true" class="leo-connector' + (' done' if index <= current else '') + '"></i>')
        style = ' aria-current="step"' if index == current else ''
        done = ' done' if index < current else ''
        parts.append(f'<span class="leo-step{done}"{style}><b>{index}</b>{item}</span>')
    st.markdown('<nav class="leo-steps" aria-label="Learning progress">' + "".join(parts) + '</nav>',
                unsafe_allow_html=True)
    return current


def live_panel(row, current, events):
    with st.container(border=True, key="agent_team"):
        st.subheader("AI Agent Team")
        latest = {}
        for event in events:
            latest.setdefault(event["agent"], event)
        # A routed handoff becomes waiting until that stage starts.
        waiting = events[0]["target"] if events and events[0]["status"] == "Completed" and row["state"] != "COMPLETED" else None
        with st.container(key="agent_status_grid"):
            for agent, icon in zip(AGENTS, AGENT_ICONS, strict=True):
                event = latest.get(agent)
                status = event["status"] if event else "Idle"
                if agent == waiting and (event is None or event["id"] < events[0]["id"]):
                    status = "Waiting"
                st.markdown(
                    f'<div class="leo-agent {status.lower()}"><span class="leo-agent-icon" aria-hidden="true">{icon}</span>'
                    f'<div><strong>{agent}</strong><small><i aria-hidden="true"></i>{status}</small></div></div>',
                    unsafe_allow_html=True,
                )
        if waiting:
            st.caption(f'{events[0]["agent"]} → {waiting}')
    with st.container(border=True, key="session_progress"):
        st.subheader("Session Progress")
        st.progress(current / 4, text=f"Step {current} of 4 · {current * 25}%")
    with st.container(border=True, key="topic_info"):
        st.subheader("Topic Info")
        st.write(row["topic"])
        st.caption(f'{row["level"]} · {SessionData.model_validate_json(row["data"]).language}')


def activity_page(events):
    if not events:
        st.info("Start a learning session to see agent execution records.")
        return
    for event in events:
        with st.container(border=True):
            st.markdown(f'**{event["agent"]} · {event["status"]}**')
            st.write(event["task"])
            st.caption(f'Started {event["started"]} · Finished {event["ended"] or "—"}')
            if event["outcome"]:
                st.write(event["outcome"])
            if event["target"] and event["status"] == "Completed":
                st.caption(f'{event["agent"]} → {event["target"]}')


def score(attempt):
    result = json.loads(attempt["result"])
    return result["score"] / result["total"]


def study_metrics(rows, attempts):
    completed = sum(row["state"] == "COMPLETED" for row in rows)
    return zip(
        ("Sessions", "Completed", "Quiz attempts", "Average score"),
        (len(rows), completed, len(attempts), f'{sum(map(score, attempts)) / len(attempts):.0%}' if attempts else "—"),
        strict=True,
    )


def latest_attempts(attempts):
    latest = {}
    for attempt in attempts:
        latest.setdefault(attempt["session_id"], attempt)
    return latest


def review_concepts(latest):
    return list(dict.fromkeys(concept for attempt in latest.values()
                              for concept in json.loads(attempt["result"])["weak_concepts"]))


def dashboard_metrics(rows, attempts):
    tiles = "".join(f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in study_metrics(rows, attempts))
    st.markdown(f'<dl class="leo-dashboard-metrics">{tiles}</dl>', unsafe_allow_html=True)


def analytics(rows, attempts, *, detail=False):
    for column, (title, value) in zip(st.columns(4), study_metrics(rows, attempts), strict=True):
        column.metric(title, value)
    if not attempts:
        st.info("Complete a quiz to see your performance and study recommendations.")
        return
    by_topic = defaultdict(list)
    for attempt in attempts:
        by_topic[attempt["topic"]].append(score(attempt))
    if detail:
        st.subheader("Quiz trend")
        trend = [{"Attempt": i, "Score (%)": round(score(a) * 100, 1)}
                 for i, a in enumerate(reversed(attempts), 1)]
        st.line_chart(trend, x="Attempt", y="Score (%)")
        st.subheader("Performance by topic")
        st.dataframe([{"Topic": topic, "Attempts": len(values), "Average": f'{sum(values) / len(values):.0%}'}
                      for topic, values in by_topic.items()], width="stretch", hide_index=True)
    concepts = review_concepts(latest_attempts(attempts))
    st.subheader("Concepts to review")
    if concepts:
        st.write(", ".join(concepts))
        st.caption("Recommended next topics")
        for concept in concepts[:5]:
            if st.button(concept, key=f'recommend_{concept}'):
                st.session_state.update(next_topic=concept, active_session=None, next_page="Learning Session")
                st.rerun()
    else:
        st.caption("No weak concepts in your latest attempts.")


def feedback(attempt):
    result = json.loads(attempt["result"])
    quiz = {q["id"]: q for q in json.loads(attempt["quiz"])["questions"]}
    answers = json.loads(attempt["answers"])
    correct, total = result["score"], result["total"]
    st.subheader("Quiz feedback")
    st.write(f"Score: {correct}/{total} · {correct / total:.0%}")
    st.caption(f"{total} questions · {correct} correct · {total - correct} incorrect")
    with st.container(key=f'feedback_scroll_{attempt["quiz_id"]}', border=True):
        for item in result["feedback"]:
            q = quiz[item["id"]]
            with st.container(border=True):
                css, outcome = ("correct", "Correct") if item["correct"] else ("incorrect", "Incorrect")
                st.markdown(f'<span class="leo-{css}">{outcome}</span>', unsafe_allow_html=True)
                st.write(q["question"])
                st.write(f'Your answer: {answers[str(q["id"])]}')
                st.write(f'Correct answer: {q["correct_answer"]}')
                st.write(item["feedback"])
                if (not item["correct"] and q["explanation"] != item["feedback"]):
                    st.caption(q["explanation"])
        if result["weak_concepts"]:
            st.write("Concepts to review: " + ", ".join(result["weak_concepts"]))
        if result.get("suggestions"):
            st.write("Improvement suggestions")
            for suggestion in result["suggestions"]:
                st.write(suggestion)
        if result.get("summary"):
            st.caption(result["summary"])


def quiz_review(attempts):
    with st.container(key="quiz_review"):
        if not attempts:
            with st.container(border=True):
                st.subheader("No quiz results yet")
                st.write("Submit your first quiz in Learning Session to review your score here.")
            return

        def attempt_label(index):
            attempt = attempts[index]
            date = datetime.fromisoformat(attempt["created"]).strftime("%d %b %Y, %H:%M UTC")
            return f'{attempt["topic"]} · {score(attempt):.0%} · {date}'

        index = st.selectbox("Quiz attempt", range(len(attempts)), format_func=attempt_label)
        if index is None:
            return
        attempt = attempts[index]
        result = json.loads(attempt["result"])
        questions = {q["id"]: q for q in json.loads(attempt["quiz"])["questions"]}
        answers = json.loads(attempt["answers"])
        correct, total = result["score"], result["total"]
        percentage = round(correct / total * 100)
        st.markdown(
            '<div class="leo-review-summary">'
            f'<h2>{escape(attempt["topic"])}</h2>'
            f'<p>{escape(attempt["difficulty"])}</p>'
            '<div class="leo-review-metrics">'
            f'<div class="leo-review-score"><span>Score</span><strong>{percentage}%</strong></div>'
            f'<div><span>Correct</span><strong>{correct}</strong></div>'
            f'<div><span>Incorrect</span><strong>{total - correct}</strong></div>'
            f'<div><span>Questions</span><strong>{total}</strong></div>'
            '</div></div>', unsafe_allow_html=True,
        )
        if result["weak_concepts"] or result.get("suggestions") or result.get("summary"):
            with st.expander("Study takeaways"):
                if result["weak_concepts"]:
                    st.write("Concepts to review — " + ", ".join(result["weak_concepts"]))
                for suggestion in result.get("suggestions", []):
                    st.write(suggestion)
                if result.get("summary"):
                    st.caption(result["summary"])

        st.subheader("Question review")
        selected = st.segmented_control(
            "Filter questions", ["All", "Incorrect", "Correct"],
            default="All", label_visibility="collapsed",
        )
        entries = {item["id"]: item for item in result["feedback"]}
        visible = [
            (number, question, entries[qid]) for number, (qid, question) in enumerate(questions.items(), 1)
            if selected not in {"Correct", "Incorrect"} or entries[qid]["correct"] == (selected == "Correct")
        ]
        if not visible:
            st.info(f"No {selected.lower()} answers in this attempt.")
            return
        with st.container(key=f'feedback_scroll_{attempt["quiz_id"]}'):
            for number, question, entry in visible:
                answer = answers[str(question["id"])]
                style, label = ("correct", "Correct") if entry["correct"] else ("incorrect", "Incorrect")
                explanation = escape(entry["feedback"])
                if not entry["correct"] and question["explanation"] != entry["feedback"]:
                    explanation += "<br><br>" + escape(question["explanation"])
                st.markdown(
                    f'<section class="leo-review-question {style}">'
                    f'<p class="leo-review-question-top"><span>Question {number}</span>'
                    f'<strong>{label}</strong></p>'
                    f'<h4>{escape(question["question"])}</h4>'
                    '<dl><div><dt>Your answer</dt>'
                    f'<dd>{escape(answer)}</dd></div><div><dt>Correct answer</dt>'
                    f'<dd>{escape(question["correct_answer"])}</dd></div></dl>'
                    f'<p class="leo-review-explanation">{explanation}</p></section>',
                    unsafe_allow_html=True,
                )


def lesson_content(row, data):
    with st.container(border=True, key="lesson_card"):
        st.markdown('<span class="leo-badge">✧ Explainer · Lesson ready</span>', unsafe_allow_html=True)
        st.header(row["topic"])
        st.caption(f'{row["level"]} · {data.language}')
        with st.container(key="lesson_scroll", border=True):
            objectives = (data.plan or {}).get("objectives", [])[:5]
            if not objectives:
                st.markdown(data.lesson)
            else:
                body, map_panel = st.columns([2.1, 1])
                with body:
                    st.markdown(data.lesson)
                with map_panel:
                    nodes = "".join(f'<div><i aria-hidden="true">◆</i>{escape(item)}</div>' for item in objectives)
                    st.markdown(
                        '<aside class="leo-map" aria-label="Learning objectives"><strong>Learning objectives</strong>'
                        f'<div class="leo-concepts">{nodes}</div></aside>', unsafe_allow_html=True
                    )
            for question, answer in data.conversation:
                with st.chat_message("user"):
                    st.write(question)
                with st.chat_message("assistant"):
                    st.markdown(answer)
        if data.rounds:
            st.caption(f"Review round {data.rounds} of {learning.review_limit()}")
        with st.expander("Coordinator’s plan"):
            st.write(data.plan["plan"])
            for item in data.plan.get("outline", []):
                st.write(item)
