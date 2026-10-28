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


# ---------------------------------------------------------------- staff: every learner's attempts at one assessment

def _utc_naive(value: str | None, field: str, *, end_of_range: bool = False):
    """UTC ISO 8601 text -> naive UTC. `Z` or any offset is converted; no offset is read as UTC; a bare date covers the whole day."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().replace("Z", "+00:00").replace("z", "+00:00")
    try:
        if len(text) == 10:
            day = datetime.fromisoformat(text)
            return day.replace(hour=23, minute=59, second=59, microsecond=999999) if end_of_range else day
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(422, f"{field} must be an ISO 8601 UTC time such as 2026-10-26T07:22:00Z")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def _like(text: str) -> str:
    return "%" + text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_submissions(db, training, aid, *, status="all", search=None, from_date=None, to_date=None, order="newest", page=1, page_size=20):
    """Staff view: one row per attempt, any learner. Paginated in SQL; the name comes from the learner's enrolment."""
    from sqlalchemy import func, or_

    from app.models.training_model import TrainingEnrolment
    from app.repository.query_utils import build_pagination_meta

    assessment = next((a for a in training.assessments or [] if str(a.get("id")) == str(aid)), None)
    if assessment is None:
        raise HTTPException(404, "Assessment not found")
    start, end = _utc_naive(from_date, "from_date"), _utc_naive(to_date, "to_date", end_of_range=True)
    if start and end and start > end:
        raise HTTPException(422, "from_date is after to_date")

    S = TrainingAssessmentSubmission
    names = (db.query(func.lower(TrainingEnrolment.participant_email).label("email"), func.max(TrainingEnrolment.participant_name).label("name"))
             .filter(TrainingEnrolment.training_id == training.id).group_by(func.lower(TrainingEnrolment.participant_email)).subquery())
    state = func.coalesce(S.review_snapshot["status"].as_string(), "pending")
    base = (db.query(S, names.c.name).outerjoin(names, names.c.email == func.lower(S.participant_email))
            .filter(S.training_id == training.id, S.assessment_id == str(aid)))
    if search and search.strip():
        pattern = _like(search)
        base = base.filter(or_(names.c.name.ilike(pattern, escape="\\"), S.participant_email.ilike(pattern, escape="\\")))
    if start:
        base = base.filter(S.submitted_at >= start)
    if end:
        base = base.filter(S.submitted_at <= end)

    evaluated = base.filter(state == "evaluated").count()
    everything = base.count()
    counts = {"all": everything, "pending": everything - evaluated, "evaluated": evaluated}

    query = base
    if status == "evaluated":
        query = query.filter(state == "evaluated")
    elif status == "pending":
        query = query.filter(state != "evaluated")
    total = counts[status]
    newest = order != "oldest"
    query = query.order_by(S.submitted_at.desc() if newest else S.submitted_at.asc(), S.id.desc() if newest else S.id.asc())
    rows = query.offset((page - 1) * page_size).limit(page_size).all()

    ordinals = {}
    unnumbered = {r.participant_email for r, _ in rows if not (r.review_snapshot or {}).get("attempt_number")}
    if unnumbered:
        seen = {}
        for r in (db.query(S).filter(S.training_id == training.id, S.assessment_id == str(aid), S.participant_email.in_(unnumbered))
                  .order_by(S.submitted_at.asc(), S.id.asc()).all()):
            seen[r.participant_email] = seen.get(r.participant_email, 0) + 1
            ordinals[r.id] = seen[r.participant_email]

    items = []
    for row, name in rows:
        snap = row.review_snapshot or {}
        legacy = not snap
        state_text = "evaluated" if snap.get("status") == "evaluated" else "pending"
        total_points = snap.get("total_points")
        try:
            score = int(row.score) if not legacy and row.score is not None else None
        except (TypeError, ValueError):
            score = None
        items.append({
            "submission_id": str(row.id), "attempt_id": str(row.id), "learner_name": name, "learner_email": row.participant_email,
            "attempt_number": snap.get("attempt_number") or ordinals.get(row.id), "status": state_text,
            "needs_manual_grading": state_text == "pending", "score": score, "total_points": total_points,
            "score_percent": int(score * 100 / total_points) if score is not None and total_points else None,
            "passed": row.passed if state_text == "evaluated" and score is not None else None,
            "submitted_at": row.submitted_at, "is_legacy": legacy,
        })
    return {"assessment_id": str(aid), "assessment_title": assessment.get("title"), "items": items,
            "pagination": build_pagination_meta(total, page, page_size), "status_counts": counts}
