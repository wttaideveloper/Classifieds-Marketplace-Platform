# app/services/admin_service.py
from fastapi import status, HTTPException
from datetime import datetime, timedelta
import secrets
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.services.email_service import send_email
from app.repository.admin_repo import (
    get_admin_by_email,
    get_admin_by_reset_token,
    get_admin_by_id,
    update_admin,
    get_all_businesses,
    get_business_by_id,
    approve_business,
    reject_business,
    suspend_business,
    reactivate_business,
    get_business_with_merchant
)
from app.models.customer_model import Customer
from app.models.merchant_model import Merchant
from app.models.admin_model import Admin
from app.repository.customer_repo import get_all_users
from app.exceptions.custom_exception import CustomException
from app.core.security import verify_password, create_access_token, hash_password, create_refresh_token

def admin_login_service(db, payload):

    admin = get_admin_by_email(db, payload.email)

    if not admin:
        raise CustomException(status.HTTP_404_NOT_FOUND, "Admin not found")

    if not verify_password(payload.password, admin.password):
        raise CustomException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token_data = {
        "id": admin.id,
        "email": admin.email,
        "role": "admin"
    }

    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    return {
        "success": True,
        "message": "Admin login successful",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

    return {
        "success": True,
        "message": "Admin login successful",
        "access_token": token
    }

# FORGOT PASSWORD
def forgot_password_admin_service(db, email):

    admin = get_admin_by_email(db, email)

    if not admin:
        raise CustomException(404, "Admin not found")

    reset_token = secrets.token_urlsafe(32)

    admin.resetToken = reset_token
    admin.resetTokenExpiry = datetime.utcnow() + timedelta(minutes=15)

    update_admin(db, admin)

    send_email(admin.email, reset_token)

    return {
        "success": True,
        "message": "Reset password link sent successfully",
        "resetToken": reset_token
    }

# RESET PASSWORD
def reset_password_admin_service(
    db,
    reset_token,
    new_password,
    confirm_password
):

    if new_password != confirm_password:
        raise CustomException(400, "Passwords do not match")

    admin = get_admin_by_reset_token(db, reset_token)

    if not admin:
        raise CustomException(400, "Invalid token")

    if admin.resetTokenExpiry < datetime.utcnow():
        raise CustomException(400, "Token expired")

    admin.password = hash_password(new_password)
    admin.resetToken = None
    admin.resetTokenExpiry = None

    update_admin(db, admin)

    return {
        "success": True,
        "message": "Password reset successful"
    }


# CHANGE PASSWORD
def change_password_admin_service(
    db,
    admin_id,
    current_password,
    new_password,
    confirm_password
):

    admin = get_admin_by_id(db, admin_id)

    if not admin:
        raise CustomException(404, "Admin not found")

    if not verify_password(current_password, admin.password):
        raise CustomException(401, "Current password incorrect")

    if new_password != confirm_password:
        raise CustomException(400, "Passwords do not match")

    admin.password = hash_password(new_password)

    update_admin(db, admin)

    return {
        "success": True,
        "message": "Password changed successfully"
    }


# LOGOUT
def logout_admin_service(token, current_user):
    return {
        "success": True,
        "message": "Admin logout successful"
    }

# GET PROFILE
def get_admin_profile_service(db, admin_id):

    admin = get_admin_by_id(db, admin_id)

    if not admin:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin not found"
        )

    return {
        "success": True,
        "data": {
            "id": admin.id,
            "name": admin.name,
            "email": admin.email
        }
    }


# UPDATE PROFILE
def update_admin_profile_service(db, admin_id, payload):

    admin = get_admin_by_id(db, admin_id)

    if not admin:
        raise CustomException(404, "Admin not found")

    existing_email = get_admin_by_email(db, payload.email)

    if existing_email and existing_email.id != admin.id:
        raise CustomException(400, "Email already exists")

    admin.name = payload.name
    admin.email = payload.email

    update_admin(db, admin)

    return {
        "success": True,
        "message": "Profile updated successfully",
        "data": {
            "id": admin.id,
            "name": admin.name,
            "email": admin.email
        }
    }


def admin_get_users_service(db, search, role, status, page, limit):
    total, users = get_all_users(db, search, role, status, page, limit)
    return {
        'total': total,
        'page': page,
        'limit': limit,
        'data': users
    }

def admin_get_user_details_service(db, user_id):

    # CUSTOMER
    customer = db.query(Customer).filter(
        Customer.id == user_id
    ).first()

    if customer:
        return {
            "id": customer.id,
            "name": f"{customer.firstName} {customer.lastName}",
            "email": customer.email,
            "role": "customer",
            "status": customer.status,
            "createdAt": customer.createdAt
        }

    # MERCHANT
    merchant = db.query(Merchant).filter(
        Merchant.id == user_id
    ).first()

    if merchant:
        return {
            "id": merchant.id,
            "name": merchant.businessName,
            "email": merchant.businessEmail,
            "role": "merchant",
            "status": merchant.status,
            "createdAt": merchant.createdAt
        }

    # ADMIN
    admin = db.query(Admin).filter(
        Admin.id == user_id
    ).first()

    if admin:
        return {
            "id": admin.id,
            "name": admin.name,
            "email": admin.email,
            "role": "admin",
            "status": admin.status,
            "createdAt": admin.createdAt
        }

    raise CustomException(
        status.HTTP_404_NOT_FOUND,
        "User not found"
    )

def admin_update_user_status_service(db, user_id, payload):

    new_status = payload.status.lower()

    # CUSTOMER
    customer = db.query(Customer).filter(
        Customer.id == user_id
    ).first()

    if customer:
        customer.status = new_status
        db.commit()
        db.refresh(customer)

        return {
            "success": True,
            "message": "Customer status updated successfully",
            "data": {
                "id": customer.id,
                "role": "customer",
                "status": customer.status
            }
        }

    # MERCHANT
    merchant = db.query(Merchant).filter(
        Merchant.id == user_id
    ).first()

    if merchant:
        merchant.status = new_status
        db.commit()
        db.refresh(merchant)

        return {
            "success": True,
            "message": "Merchant status updated successfully",
            "data": {
                "id": merchant.id,
                "role": "merchant",
                "status": merchant.status
            }
        }

    # ADMIN
    admin = db.query(Admin).filter(
        Admin.id == user_id
    ).first()

    if admin:
        admin.status = new_status
        db.commit()
        db.refresh(admin)

        return {
            "success": True,
            "message": "Admin status updated successfully",
            "data": {
                "id": admin.id,
                "role": "admin",
                "status": admin.status
            }
        }

    raise CustomException(
        status.HTTP_404_NOT_FOUND,
        "User not found"
    )


def admin_get_merchants_service(
    db,
    search=None,
    status=None,
    page=1,
    limit=10
):

    query = db.query(Merchant)

    # SEARCH
    if search:
        query = query.filter(
            or_(
                Merchant.businessName.ilike(f"%{search}%"),
                Merchant.businessEmail.ilike(f"%{search}%")
            )
        )

    # STATUS FILTER
    if status:
        query = query.filter(
            Merchant.status == status.lower()
        )

    # TOTAL COUNT
    total = query.count()

    # PAGINATION
    merchants = query.order_by(
        Merchant.createdAt.desc()
    ).offset(
        (page - 1) * limit
    ).limit(limit).all()

    result = []

    for merchant in merchants:
        result.append({
            "id": merchant.id,
            "name": merchant.businessName,
            "email": merchant.businessEmail,
            "status": merchant.status,
            "createdAt": str(merchant.createdAt)
        })

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": result
    }



def admin_get_merchant_details_service(db, merchant_id):

    merchant = db.query(Merchant).filter(
        Merchant.id == merchant_id
    ).first()

    if not merchant:
        raise CustomException(
            status.HTTP_404_NOT_FOUND,
            "Merchant not found"
        )

    return {
        "id": merchant.id,
        "name": merchant.businessName,
        "email": merchant.businessEmail,
        "mobileNumber": merchant.mobileNumber,
        "status": merchant.status,
        "createdAt": str(merchant.createdAt)
    }

def fetch_businesses_service(
    db: Session,
    search: str,
    status: str,
    category: str,
    page: int,
    limit: int
):
    try:
        skip = (page - 1) * limit

        total, businesses = get_all_businesses(
            db=db,
            search=search,
            status=status,
            category=category,
            skip=skip,
            limit=limit
        )

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "data": businesses
        }

    except Exception as e:
        raise Exception(f"Service Error: {str(e)}")
    
def fetch_business_detail_service(db: Session, business_id):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found"
            )

        return business

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Service Error: {str(e)}"
        )
    
def approve_business_service(db: Session, business_id):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found"
            )

        if business.status == "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Business already approved"
            )

        updated_business = approve_business(db, business)

        return updated_business

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Service Error: {str(e)}"
        )

def reject_business_service(db: Session, business_id, reason: str = None):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found"
            )

        if business.status == "rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Business already rejected"
            )

        if business.status == "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Approved business cannot be rejected"
            )

        updated_business = reject_business(db, business, reason)

        return updated_business

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Service Error: {str(e)}"
        )

def suspend_business_service(db: Session, business_id, reason: str = None):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found"
            )

        if business.status == "suspended":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Business already suspended"
            )

        if business.status == "rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rejected business cannot be suspended"
            )

        updated_business = suspend_business(db, business, reason)

        return updated_business

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Service Error: {str(e)}"
        )

def reactivate_business_service(db: Session, business_id):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found"
            )

        if business.status != "suspended":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only suspended businesses can be reactivated"
            )

        updated_business = reactivate_business(db, business)

        return updated_business

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Service Error: {str(e)}"
        )
    
def get_associated_merchant_service(db: Session, business_id):
    try:
        business = get_business_with_merchant(db, business_id)

        if not business:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business not found"
            )

        if not business.merchant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No merchant associated with this business"
            )

        return business.merchant

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Service Error: {str(e)}"
        )