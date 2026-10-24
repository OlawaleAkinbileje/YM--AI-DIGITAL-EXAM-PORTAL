"""
YEYE MOREMI OODUA AI & DIGITAL TRAINING HUB
Grading Engine
Dynamically evaluates candidate submissions against the specific exam selected.
"""

from db import get_questions_by_exam, get_exam

def grade_submission(form_data: dict, exam_id: int = 1) -> tuple[int, int, float, bool]:
    """
    Grades candidate submission against the specified exam questions.
    Returns:
        score (int): Number of correct answers
        total_questions (int): Total questions in this exam
        percentage (float): Score percentage rounded to 2 decimal places
        passed (bool): True if percentage >= exam passing cutoff
    """
    questions = get_questions_by_exam(exam_id)
    exam = get_exam(exam_id)
    total_questions = len(questions)

    if total_questions == 0:
        return 0, 0, 0.0, False

    score = 0
    for q in questions:
        q_id = q["id"]
        correct_ans = q["answer"]
        if form_data.get(q_id) == correct_ans:
            score += 1

    percentage = round((score / total_questions) * 100, 2)
    cutoff = float(exam["passing_cutoff"]) if exam else 85.0
    passed = percentage >= cutoff

    return score, total_questions, percentage, passed