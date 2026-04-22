from app.models.customer_model import Customer
from sqlalchemy.orm import Session

def get_customer_by_email(db: Session, email: str):
    return db.query(Customer).filter(Customer.email == email).first()

def create_customer(db: Session, customer: Customer):
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer

def update_customer(db: Session, customer):
    db.commit()
    db.refresh(customer)
    return customer