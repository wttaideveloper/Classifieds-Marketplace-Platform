from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.cart_schema import (
    AddToCartSchema,
    CartItemEnvelopeResponse,
    CartMessageResponse,
    CartSummaryResponse,
    CheckoutPreparationResponse,
    UpdateCartItemSchema,
)
from app.services.cart_service import (
    add_to_cart_service,
    checkout_cart_service,
    clear_cart_service,
    get_cart_service,
    remove_cart_item_service,
    update_cart_item_service,
)

router = APIRouter(tags=["Cart"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CartItemEnvelopeResponse)
def add_to_cart(
    payload: AddToCartSchema,
    customer_id: str = Query(..., description="Customer UUID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return add_to_cart_service(db=db, customer_id=customer_id, payload=payload)


@router.get("", status_code=status.HTTP_200_OK, response_model=CartSummaryResponse)
def get_cart(
    customer_id: str = Query(..., description="Customer UUID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_cart_service(db=db, customer_id=customer_id)


@router.delete("/clear", status_code=status.HTTP_200_OK, response_model=CartMessageResponse)
def clear_cart(
    customer_id: str = Query(..., description="Customer UUID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return clear_cart_service(db=db, customer_id=customer_id)


@router.post("/checkout", status_code=status.HTTP_200_OK, response_model=CheckoutPreparationResponse)
def checkout_cart(
    customer_id: str = Query(..., description="Customer UUID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return checkout_cart_service(db=db, customer_id=customer_id)


@router.put("/{item_id}", status_code=status.HTTP_200_OK, response_model=CartItemEnvelopeResponse)
def update_cart_item(
    item_id: str,
    payload: UpdateCartItemSchema,
    customer_id: str = Query(..., description="Customer UUID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return update_cart_item_service(
        db=db,
        customer_id=customer_id,
        item_id=item_id,
        payload=payload,
    )


@router.delete("/{item_id}", status_code=status.HTTP_200_OK, response_model=CartMessageResponse)
def remove_cart_item(
    item_id: str,
    customer_id: str = Query(..., description="Customer UUID"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return remove_cart_item_service(db=db, customer_id=customer_id, item_id=item_id)
