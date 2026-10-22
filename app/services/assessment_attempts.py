"""Read-only learner assessment attempts, based on submission-time snapshots."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func

from app.models.training_model import TrainingAssessmentSubmission, TrainingAssignmentSubmission


def _allowed(snapshot):
    if not snapshot or snapshot.get("status") != "evaluated":
        return False
    assessment = snapshot.get("assessment") or {}
    mode = assessment.get("publication") or assessment.get("result_publication") or "immediate"
    if mode == "immediate":
        return True
    if mode == "scheduled" and assessment.get("publish_at"):
        try:
            when = datetime.fromisoformat(str(assessment["publish_at"]).replace("Z", "+00:00"))
            return datetime.now(timezone.utc) >= (when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when)
        except ValueError:
            return False
    return False


def summary(row, number=None):
    snap = row.review_snapshot or {}
    available = _allowed(snap)
    total = snap.get("total_points")
    try:
        score = int(row.score) if available and row.score is not None else None
    except (ValueError, TypeError):
        score = None
    return {"id": str(row.id), "attempt_number": snap.get("attempt_number") or number,
            "submitted_at": row.submitted_at, "score": score, "total_points": total,
            "score_percent": int(score * 100 / total) if score is not None and total else None,
            "passed": row.passed if available and score is not None else None,
            "status": snap.get("status", "pending"), "can_review": available}


def assignment_summaries(db, tid, email):
    """Fetch attempt summaries in a fixed number of queries, independent of assignment count."""
    if not email:
        return {}, {}
    assessment_rows = (db.query(TrainingAssessmentSubmission.id.label("submission_id"),
        func.count().over(partition_by=TrainingAssessmentSubmission.assessment_id).label("attempt_count"),
        func.row_number().over(partition_by=TrainingAssessmentSubmission.assessment_id,
            order_by=(TrainingAssessmentSubmission.submitted_at.desc(), TrainingAssessmentSubmission.id.desc())).label("rank"))
        .filter(TrainingAssessmentSubmission.training_id == tid, TrainingAssessmentSubmission.participant_email == email).subquery())
    # A ranked subquery of complete entities is expensive to map; select IDs then
    # fetch the bounded set of latest rows in one query.
    latest = (db.query(TrainingAssessmentSubmission, assessment_rows.c.attempt_count)
              .join(assessment_rows, assessment_rows.c.submission_id == TrainingAssessmentSubmission.id)
              .filter(assessment_rows.c.rank == 1).all())
    counts = dict(db.query(TrainingAssignmentSubmission.assignment_id, func.count(TrainingAssignmentSubmission.id))
        .filter(TrainingAssignmentSubmission.training_id == tid, TrainingAssignmentSubmission.participant_email == email)
        .group_by(TrainingAssignmentSubmission.assignment_id).all())
    reviewable = {assessment_id for assessment_id, snapshot in
                  db.query(TrainingAssessmentSubmission.assessment_id, TrainingAssessmentSubmission.review_snapshot)
                  .filter(TrainingAssessmentSubmission.training_id == tid,
                          TrainingAssessmentSubmission.participant_email == email).all() if _allowed(snapshot)}
    return {row.assessment_id: (row, count, row.assessment_id in reviewable) for row, count in latest}, counts


def _context(db, tid, aid, user):
    from app.services import training_service as ts
    training = ts._get_training_or_404(db, tid)
    email = user.get("email") if user else None
    enrol = ts._get_enrolment(db, tid, email) if email else None
    if not ts._is_active_enrolment(enrol) or ts._check_access_expiry(db, tid, email):
        raise HTTPException(403, "Active enrolment required")
    if training.status in ("draft", "cancelled", "archived"):
        raise HTTPException(403, "Training content is not available")
    if getattr(enrol, "user_id", None) and user.get("id") and str(enrol.user_id) != str(user["id"]):
        raise HTTPException(403, "Active enrolment required")
    assessment = next((a for a in training.assessments or [] if str(a.get("id")) == str(aid)), None)
    if assessment is None:
        raise HTTPException(404, "Assessment assignment not found")
    return assessment, email


def attempts(db, tid, aid, user):
    _, email = _context(db, tid, aid, user)
    rows = (db.query(TrainingAssessmentSubmission).filter_by(training_id=tid, assessment_id=str(aid), participant_email=email)
            .order_by(TrainingAssessmentSubmission.submitted_at.desc(), TrainingAssessmentSubmission.id.desc()).all())
    return [summary(row, len(rows) - i) for i, row in enumerate(rows)]


def _answer_id(value, options, multiple=False, boolean=False):
    if value is None:
        return None
    if boolean:
        text = str(value).strip().lower()
        return {"true": "true", "false": "false", "yes": "true", "no": "false",
                "y": "true", "n": "false", "1": "true", "0": "false"}.get(text)
    parts = str(value).split(",") if multiple else [str(value)]
    resolved = []
    for part in parts:
        key = part.strip().casefold()
        matches = {str(o["id"]) for o in options if o["id"] is not None and key in (str(o["id"]).casefold(), str(o["label"]).casefold())}
        if len(matches) != 1:
            return None
        resolved.append(next(iter(matches)))
    return ",".join(resolved) if multiple else resolved[0]


def review(db, tid, aid, attempt_id, user):
    _, email = _context(db, tid, aid, user)
    row = db.query(TrainingAssessmentSubmission).filter_by(id=attempt_id, training_id=tid, assessment_id=str(aid), participant_email=email).first()
    if row is None:
        raise HTTPException(404, "Attempt not found")
    snap = row.review_snapshot or {}
    saved = snap.get("assessment") or {}
    data = summary(row)
    if data["attempt_number"] is None:
        rows = attempts(db, tid, aid, user)
        data["attempt_number"] = next((r["attempt_number"] for r in rows if r["id"] == str(row.id)), None)
    answers = {str(a.get("question_id") or a.get("id")): a.get("answer") for a in row.answers or []}
    questions = []
    for question in saved.get("questions") or []:
        qid = str(question.get("id"))
        kind = question.get("question_type") or "mcq"
        options = [{"id": str(o["id"]) if o.get("id") is not None else None, "label": str(o.get("label", o.get("value", "")))} if isinstance(o, dict)
                   else {"id": str(o), "label": str(o)} for o in question.get("options") or []]
        value = answers.get(qid)
        choice = kind in ("mcq", "single_choice", "multiple_select", "true_false")
        user_answer = _answer_id(value, options, kind == "multiple_select", kind == "true_false") if choice else (str(value) if value is not None else None)
        raw_correct = question.get("correct_answer")
        correct = _answer_id(raw_correct, options, kind == "multiple_select", kind == "true_false") if choice else (str(raw_correct) if raw_correct is not None else None)
        result = (snap.get("question_results") or {}).get(qid) or {}
        questions.append({"id": qid, "question_text": question.get("question_text"), "question_type": kind,
            "points": question.get("points", 1), "options": options, "user_answer": user_answer,
            "correct_answer": correct if data["can_review"] else None,
            "explanation": question.get("explanation") if data["can_review"] else None,
            "is_correct": result.get("is_correct") if data["can_review"] else None,
            "points_awarded": result.get("points_awarded") if data["can_review"] else None})
    data.update(assignment_id=str(aid), assignment_title=saved.get("title"),
                attempts_allowed=saved.get("attempt_limit") or saved.get("attempts_allowed"),
                passing_score=snap.get("passing_points") if data["can_review"] else None,
                pass_percent=saved.get("pass_percent") if data["can_review"] else None,
                feedback=snap.get("feedback") if data["can_review"] else None, questions=questions)
    return data
