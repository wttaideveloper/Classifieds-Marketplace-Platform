"""Internal API of the learning service (/internal/v1, shared key, never routed by the gateway).

* POST /training-media/upload-field, /training-media/validate - training form upload rules (used by content's /uploads)
* POST /replica/{topic} - pushed by catalog (enterprises, locations)
"""
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from learning_service.db.database import get_db
from learning_service.startup import REPLICAS
from mskit.app import require_internal_key
from mskit.replication import subscriber_router

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


class UploadFieldQuery(BaseModel):
    user: dict
    field_key: str | None = None
    training_id: str | None = None


@router.post("/training-media/upload-field")
def upload_field(q: UploadFieldQuery, db: Session = Depends(get_db)):
    """training_form_media.resolve_upload_field + policy_cap for the caller (errors are the monolith's HTTPExceptions)."""
    from uuid import UUID

    from learning_service.services.training_form_media import policy_cap, resolve_upload_field

    field = resolve_upload_field(db, q.user, q.field_key, UUID(q.training_id) if q.training_id else None)
    return {"field": field, "cap": policy_cap(field) if field else None}


@router.post("/training-media/validate", status_code=204)
async def validate(field: str = Form(...), claimed_mime: str = Form(""), purpose: str = Form(""), file: UploadFile = File(...)):
    """training_form_media.validate_media_bytes (raises the monolith's HTTPException on a bad file)."""
    from learning_service.services.training_form_media import validate_media_bytes

    validate_media_bytes(json.loads(field), await file.read(), claimed_mime or None, purpose or None)


from learning_service.meetings_api import router as meetings_router  # noqa: E402

routers = [router, subscriber_router(REPLICAS), meetings_router]  # replicas (catalog), meetings (D7)


# review moderation queue (D6): this service's slice + moderate/delete, called by moderation
from learning_service.db.database import get_db as _get_db  # noqa: E402
from learning_service.review_queue_adapter import TrainingReviews  # noqa: E402
from mskit.review_queue import owner_router  # noqa: E402

routers = [*routers, owner_router(TrainingReviews(), _get_db)]


# replica snapshots of learning data + the learner identity fill communication asks for (D5)
from uuid import UUID as _UUID  # noqa: E402
from uuid import UUID  # noqa: E402,F811 - used by the copied _EmailIndex

from fastapi import APIRouter as _APIRouter, Depends as _Depends  # noqa: E402
from pydantic import BaseModel as _BaseModel  # noqa: E402

from learning_service.db.database import SessionLocal as _SessionLocal, get_db as _get_db2  # noqa: E402
from learning_service.startup import LEARNING_PUBLISHER  # noqa: E402
from mskit.app import require_internal_key as _key  # noqa: E402
from mskit.replication import publisher_router as _publisher_router  # noqa: E402

identity_fill_router = _APIRouter(dependencies=[_Depends(_key)], tags=["internal"])


# copied from app/services/group_chat_training.py (the tenant member index used to find a learner's account)
class _EmailIndex:
    """email -> user id from the clinic's member list, loaded once per request and only if a row needs it."""

    def __init__(self, db: Session, training, access_token: str | None):
        self._db, self._training, self._token, self._index = db, training, access_token, None

    def get(self, email: str) -> UUID | None:
        if self._index is None:
            self._index = {}
            try:
                from learning_service.services import training_recipients as members
                from learning_service.services.training_learner_identity import _tenant_of

                tenant = _tenant_of(self._db, self._training)
                if tenant is not None:
                    for user in members._lookup(members.list_tenant_users, tenant, access_token=self._token):
                        found = members._user_id(user)
                        if not found:
                            continue
                        for value in members._nested_values(user):
                            mail = str(value.get("email") or "").strip().lower()
                            if mail:
                                self._index.setdefault(mail, found)
            except Exception:
                logger.exception("could not load the clinic's members to find learner accounts")
        return self._index.get(email)



class _Fill(_BaseModel):
    enrolment_ids: list[_UUID]
    access_token: str | None = None


@identity_fill_router.post("/trainings/{training_id}/enrolments/resolve-user-ids")
def resolve_enrolment_user_ids(training_id: _UUID, body: _Fill, db=_Depends(_get_db2)):
    """group_chat_training._resolve_user_ids on the given rows, here where the rows live (commits; replica push follows)."""
    from learning_service.models.training_model import Training, TrainingEnrolment
    from learning_service.services.training_learner_identity import _from_other_rows

    training = db.get(Training, training_id)
    rows = db.query(TrainingEnrolment).filter(TrainingEnrolment.id.in_(body.enrolment_ids)).all()
    if training is not None and rows:
        index = _EmailIndex(db, training, body.access_token)
        changed = False
        for row in rows:
            if row.user_id:
                continue
            email = str(row.participant_email or "").strip().lower()
            if not email:
                continue
            found = _from_other_rows(db, email) or index.get(email)
            if found:
                row.user_id = found
                changed = True
        if changed:
            db.commit()
    return {str(r.id): (str(r.user_id) if r.user_id else None) for r in rows}


routers = [*routers, _publisher_router(LEARNING_PUBLISHER, _SessionLocal), identity_fill_router]
