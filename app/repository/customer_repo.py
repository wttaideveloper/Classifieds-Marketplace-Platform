from app.models.customer_model import Customer
from sqlalchemy.orm import Session


def get_customer_by_email(db: Session, email: str):
    return db.query(Customer).filter(Customer.email == email).first()


def get_customer_by_id(db: Session, cust_id: str):
    return db.query(Customer).filter(Customer.id == cust_id).first()


def create_customer(db: Session, customer: Customer):
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def update_customer(db: Session, customer: Customer):
    try:
        db.commit()
        db.refresh(customer)
        return customer
    except Exception:
        db.rollback()
        raise