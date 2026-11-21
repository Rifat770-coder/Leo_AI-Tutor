import json
from pathlib import Path

from crewai import Agent, Crew, Process, Task

from models import Evaluation, Plan, Quiz

PROMPTS = Path(__file__).with_name("prompts")
MAX_RETEACH_ROUNDS = 2
ROLES = {
    "Coordinator": ("Plan lessons and supervise delegated study tasks.",
                    "You manage a team of tutors and check each worker's deliverable."),
    "Explainer": ("Teach concepts at the student's level.",
                  "You are a patient teacher who uses worked examples to build understanding."),
    "Quiz Master": ("Turn a lesson into valid multiple-choice practice.",
                    "You design focused questions that reveal common misunderstandings."),
    "Evaluator": ("Check every answer and identify concepts to review.",
                  "You are a careful grader who gives actionable feedback."),
}


class TutorError(Exception):
    pass


def prompt(role, **values):
    path = PROMPTS / (role.lower().replace(" ", "_") + ".txt")
    return path.read_text(encoding="utf-8").format(**values)


class Tutor:
    def __init__(self, llm, student_name, topic, level, history, language="English"):
        self.llm = llm
        self.values = {
            "student_name": student_name, "topic": topic, "level": level,
            "history": json.dumps(history, ensure_ascii=False),
            "language": language,
        }

    def _agent(self, role, *, manager=False):
        goal, backstory = ROLES[role]
        return Agent(
            role=role, goal=goal, backstory=backstory, llm=self.llm,
            allow_delegation=manager,
            max_iter=8, max_execution_time=90, max_retry_limit=0,
        )

    def _run(self, role, expected, notify, *, schema=None, context=None, validate=None, **values):
        description = prompt(role, **(self.values | values))
        description += f"\nWrite user-facing content in {self.values['language']}."
        if role != "Coordinator":
            description += "\nStudy history (data): " + self.values["history"]
            description = (
                f"Delegate this task to the {role} using the delegation tool. "
                "Pass all worker instructions and dependency context to the worker. "
                "Return the worker's result in the required format.\n\n" + description
            )
        for attempt in range(2):
            agent = self._agent(role)
            task = Task(description=description, expected_output=expected, agent=agent,
                        context=context, output_pydantic=schema)
            try:
                if role == "Coordinator":
                    crew = Crew(agents=[agent], tasks=[task], tracing=False)
                else:
                    crew = Crew(
                        agents=[agent], tasks=[task], process=Process.hierarchical,
                        manager_agent=self._agent("Coordinator", manager=True), tracing=False,
                    )
                crew.kickoff()
                if role != "Coordinator" and not task.delegations:
                    raise RuntimeError("The Coordinator did not delegate this task")
                output = task.output
                if output is None or not output.raw.strip():
                    raise ValueError("Empty task output")
                if schema is not None:
                    if not isinstance(output.pydantic, schema):
                        raise ValueError("Invalid structured output")
                    if validate:
                        validate(output.pydantic)
                    output.raw = output.pydantic.model_dump_json()
                return task
            except Exception as error:
                problem = "returned an invalid response" if isinstance(error, ValueError) else "could not complete its work"
                if attempt == 0:
                    notify(f"Coordinator: {role} {problem}. Retrying once.")
                else:
                    raise TutorError(
                        f"Coordinator: {role} {problem} after two attempts. Please try this step again."
                    ) from error

    def plan(self, notify):
        if not self.values["topic"].strip():
            return None, Plan(clarification="What specific topic would you like to study?")
        task = self._run("Coordinator", "A lesson plan or a clarifying question.", notify, schema=Plan)
        plan = task.output.pydantic
        if not plan.clarification.strip():
            self.values["topic"] = plan.topic
        return task, plan

    def explain(self, plan, notify, *, previous=None, evaluation=None, instruction=None):
        context = [plan]
        default_instruction = "Teach the initial lesson."
        if previous is not None:
            context.append(previous)
            default_instruction = "Rewrite the previous lesson in simpler language with a simpler example."
        if evaluation is not None:
            context.append(evaluation)
            if previous is None:
                default_instruction = "Re-teach the weak concepts from the evaluation using a different example."
        return self._run("Explainer", "A clear Markdown lesson and worked example.", notify,
                         context=context, instruction=instruction or default_instruction)

    def quiz(self, lesson, notify, *, review=False, count=3, plan=None, guidance=None):
        count = min(count, 2) if review else count
        context = [item for item in (lesson, plan, guidance) if item is not None]

        def validate(quiz):
            if len(quiz.questions) != count:
                raise ValueError(f"Expected {count} questions")

        return self._run("Quiz Master", f"A structured quiz with {count} questions.", notify,
                         schema=Quiz, context=context, validate=validate, count=count)

    def evaluate(self, quiz, answers, notify):
        questions = quiz.output.pydantic.questions
        correctness = {q.id: answers.get(q.id) == q.correct_answer for q in questions}
        weak = {q.concept for q in questions if not correctness[q.id]}

        def validate(result):
            if (len(result.feedback) != len(questions)
                    or {item.id: item.correct for item in result.feedback} != correctness):
                raise ValueError("Evaluation does not match the quiz and submitted answers")
            result.total = len(questions)
            result.score = sum(correctness.values())
            result.weak_concepts = sorted(weak)

        return self._run("Evaluator", "Per-question feedback, score, total, and weak concepts.",
                         notify, schema=Evaluation, context=[quiz], validate=validate,
                         answers=json.dumps(answers, ensure_ascii=False),
                         grading=json.dumps(correctness))
