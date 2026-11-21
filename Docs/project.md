Build "Bangla Tutor", a Multi-Agent AI Tutor, as a complete GitHub-ready repository. Follow ONLY the requirements below. Do not add features beyond them. Use "Bangla Tutor" as the project name everywhere (README title, app title, repo folder name bangla-tutor).

## Goal
Bangla Tutor is a study assistant made of multiple agents that collaborate to teach a student. The student picks a topic; the agents plan, teach, quiz, and give feedback, working together, not as one bot.

## Framework
Use CrewAI to build and coordinate the agents.

## LLM provider (provider-agnostic)
Do not hardcode any provider. Configure the LLM entirely through environment variables so the user can use OpenAI, Gemini, or any custom OpenAI-compatible endpoint without code changes:
- LLM_MODEL (e.g. "openai/gpt-4o-mini", "gemini/gemini-2.0-flash", or a custom model name)
- LLM_API_KEY
- LLM_BASE_URL (optional, for custom endpoints)
Create the LLM in one place (llm.py) using CrewAI's LLM class and pass it to all agents. Document each provider's example config in README and .env.example.

## Required agents (4 distinct roles, each with its own role, goal, backstory, prompt, and behaviour)
1. Coordinator: takes the student's request and delegates to the other agents. It is the manager agent of the hierarchical process. If the request is unclear, it asks the student a clarifying question instead of delegating. If an agent stalls or fails, it retries or reassigns the work and tells the student what happened.
2. Explainer: teaches the concept clearly, adapted to the student's level.
3. Quiz Master: creates practice questions. Its output MUST be structured, using a Pydantic model (for example: topic, list of questions, each with id, question, options, correct_answer, explanation). Use CrewAI's output_pydantic.
4. Evaluator: receives the Quiz Master's questions plus the student's answers, checks them, and gives per-question feedback and an overall score.

## Orchestration pattern
Hierarchical (manager/worker): the Coordinator is the manager (manager_agent) and delegates to the Explainer, Quiz Master, and Evaluator. Document this pattern clearly.

## Handoffs (must be real, not isolated outputs)
- Explainer's explanation goes to the Quiz Master as context.
- Quiz Master's structured questions go to the Evaluator as context.
- The student's answers are passed to the Evaluator together with those questions.
Implement the handoffs with task context/dependencies in CrewAI.

## Memory
Remember the student (name) and the topic across the session, and across sessions if possible (e.g. a local JSON file plus CrewAI memory). Past topics and scores must be available to the agents as context.

## Prompt templates
One separate prompt template per role (Coordinator, Explainer, Quiz Master, Evaluator) stored in a prompts/ folder and loaded with variables such as {student_name}, {topic}, {level}, {explanation}, {questions}, {answers}.

## Tools
Optional. Do not add tools unless needed.

## Graceful problem handling (managed by the Coordinator)
- Unclear or empty request: the Coordinator asks the student to clarify.
- Agent stalls or errors: use max_iter, max_execution_time, and retry; the Coordinator reports the problem and continues or retries.
- Invalid or unparsable Quiz Master output: retry, then report clearly.
- Never crash the UI; show a friendly message.

## Interface
Streamlit app. It must clearly show which agent is working at each step (status labels such as "Coordinator is planning", "Explainer is teaching", "Quiz Master is creating questions", "Evaluator is checking") and show each agent's output and the handoffs between them. Flow: student enters name, picks a topic, then explanation, then quiz, then the student answers, then evaluation and feedback.

## Bonus (implement both)
1. Feedback loop: if the Evaluator finds weak answers, it sends the weak concepts back to the Explainer for re-teaching, followed by a new short quiz. Limit the loop to a maximum number of rounds.
2. Human-in-the-loop: the student can intervene mid-run (e.g. choose difficulty or ask for a simpler explanation after the explanation step, or skip or request re-teaching).

## Deliverables
- Clean project structure with code and requirements.txt (pinned or compatible versions).
- No API keys committed: read everything from environment variables, provide .env.example, and add .env to .gitignore.
- README.md containing: project overview; the agents and their roles; an architecture diagram showing how the agents interact (Mermaid); the orchestration pattern used and why; memory and prompt template explanation; LLM provider configuration examples; how to set up and run (install, set env, streamlit run); and a short note on the bonus features.

## Process
Show the project structure first, then build all files, and tell me how to run and test. Test one full session end to end: topic selection, explanation, quiz, answering, feedback, and the re-teaching loop. If anything is unclear, ask me before assuming.