"""Authenticated learner training routes."""
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.api.v1.endpoints.training import my_enrolments
from app.services.training_service import show_training_qr_service, toggle_training_wishlist_service

router = APIRouter(tags=["Trainings"])
router.add_api_route("/trainings", my_enrolments, methods=["GET"])


@router.post("/trainings/{training_id}/qr-show")
def show_qr(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return show_training_qr_service(db, training_id, current_user)


@router.post("/trainings/{training_id}/wishlist")
def toggle_wishlist(training_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return toggle_training_wishlist_service(db, UUID(str(current_user["id"])), training_id)
