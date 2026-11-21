from pydantic import BaseModel, Field, model_validator


class Plan(BaseModel):
    topic: str = ""
    plan: str = ""
    clarification: str = ""
    objectives: list[str] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_plan(self):
        if self.clarification.strip():
            return self
        if not self.topic.strip() or not self.plan.strip():
            raise ValueError("Provide a topic and plan or a clarifying question.")
        return self


class Question(BaseModel):
    id: int = Field(ge=1)
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    correct_answer: str
    explanation: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    session_id: str = ""
    difficulty: str = ""

    @model_validator(mode="after")
    def check_options(self):
        if any(not option.strip() for option in self.options):
            raise ValueError("Options must contain text.")
        if len(set(self.options)) != len(self.options) or self.correct_answer not in self.options:
            raise ValueError("Use distinct options and a correct answer matching one option.")
        return self


class Quiz(BaseModel):
    topic: str = Field(min_length=1)
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def check_ids(self):
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique.")
        return self


class QuestionFeedback(BaseModel):
    id: int
    correct: bool
    feedback: str = Field(min_length=1)


class Evaluation(BaseModel):
    feedback: list[QuestionFeedback]
    score: int = Field(ge=0)
    total: int = Field(ge=1)
    weak_concepts: list[str]
    suggestions: list[str] = Field(default_factory=list)
    summary: str = ""
