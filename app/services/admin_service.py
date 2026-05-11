# app/services/admin_service.py
from fastapi import status as http_status, HTTPException
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
    get_business_with_merchant,
    get_all_listings_repo,
    get_listing_by_id_repo,
    approve_listing_repo,
    reject_listing_repo,
    suspend_listing_repo,
    reactivate_listing_repo,
    create_category_repo,
    get_category_by_id_repo
)
from app.models.customer_model import Customer
from app.models.merchant_model import Merchant
from app.models.admin_model import Admin
from app.repository.customer_repo import get_all_users
from app.exceptions.custom_exception import CustomException
from app.core.security import verify_password, create_access_token, hash_password, create_refresh_token

# ADMIN LOGIN
def admin_login_service(db, payload):

    admin = get_admin_by_email(db, payload.email)

    if not admin:
        raise CustomException(http_status.HTTP_404_NOT_FOUND, "Admin not found")

    if not verify_password(payload.password, admin.password):
        raise CustomException(http_status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

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
            http_status.HTTP_404_NOT_FOUND,
            "Admin not found"
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

# ADMIN GET USERS
def admin_get_users_service(db, search, role, status, page, limit):
    total, users = get_all_users(db, search, role, status, page, limit)
    return {
        'total': total,
        'page': page,
        'limit': limit,
        'data': users
    }

# ADMIN GET USER DETAILS
def admin_get_user_details_service(db, user_id):

    customer = db.query(Customer).filter(Customer.id == user_id).first()
    if customer:
        return {
            "id": customer.id,
            "name": f"{customer.firstName} {customer.lastName}",
            "email": customer.email,
            "role": "customer",
            "status": customer.status,
            "created_at": customer.createdAt
        }

    merchant = db.query(Merchant).filter(Merchant.id == user_id).first()
    if merchant:
        return {
            "id": merchant.id,
            "name": merchant.businessName,
            "email": merchant.businessEmail,
            "role": "merchant",
            "status": merchant.status,
            "created_at": merchant.createdAt
        }

    admin = db.query(Admin).filter(Admin.id == user_id).first()
    if admin:
        return {
            "id": admin.id,
            "name": admin.name,
            "email": admin.email,
            "role": "admin",
            "status": admin.status,
            "created_at": admin.createdAt
        }

    raise CustomException(http_status.HTTP_404_NOT_FOUND, "User not found")

# ADMIN UPDATE USER STATUS
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
        http_status.HTTP_404_NOT_FOUND,
        "User not found"
    )

# ADMIN GET MERCHANTS
def admin_get_merchants_service(
    db,
    search=None,
    status=None,
    page=1,
    limit=10
):

    query = db.query(Merchant)

    if search:
        query = query.filter(
            or_(
                Merchant.businessName.ilike(f"%{search}%"),
                Merchant.businessEmail.ilike(f"%{search}%")
            )
        )

    if status:
        query = query.filter(Merchant.status == status.lower())

    total = query.count()

    merchants = query.order_by(
        Merchant.createdAt.desc()
    ).offset(
        (page - 1) * limit
    ).limit(limit).all()

    result = []

    for m in merchants:
        result.append({
            "id": m.id,
            "full_name": m.businessName,
            "business_email": m.businessEmail,
            "status": m.status,
            "created_at": m.createdAt
        })

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": result
    }

# ADMIN GET MERCHANT DETAILS
def admin_get_merchant_details_service(db, merchant_id):

    merchant = db.query(Merchant).filter(
        Merchant.id == merchant_id
    ).first()

    if not merchant:
        raise CustomException(
            http_status.HTTP_404_NOT_FOUND,
            "Merchant not found"
        )

    return {
        "id": merchant.id,
        "full_name": merchant.businessName,
        "business_email": merchant.businessEmail,
        "mobile_number": merchant.mobileNumber,
        "status": merchant.status,
        "created_at": merchant.createdAt
    }

# FETCH BUSINESSES
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

        # normalize response to match schema (BusinessResponse)
        result = []
        for b in businesses:
            result.append({
                "id": b.id,
                "name": b.name,
                "category": b.category,
                "status": b.status,
                "created_at": b.created_at
            })

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "data": result
        }

    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Service Error: {str(e)}"
        )

# FETCH BUSINESS DETAIL   
def fetch_business_detail_service(db: Session, business_id):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )

        return {
            "id": business.id,
            "name": business.name,
            "category": business.category,
            "status": business.status,
            "created_at": business.created_at
        }

    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Service Error: {str(e)}"
        )

# APPROVE BUSINESS
def approve_business_service(db: Session, business_id):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )

        if business.status == "approved":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Business already approved"
            )

        updated_business = approve_business(db, business)

        return {
            "id": updated_business.id,
            "status": updated_business.status,
            "approved_at": updated_business.approved_at
        }

    except HTTPException:
        raise

    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Service Error: {str(e)}"
        )

# REJECT BUSINESS 
def reject_business_service(db: Session, business_id, reason: str = None):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )

        if business.status == "rejected":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Business already rejected"
            )

        if business.status == "approved":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Approved business cannot be rejected"
            )

        updated_business = reject_business(db, business, reason)

        return {
            "id": updated_business.id,
            "status": updated_business.status,
            "rejection_reason": updated_business.rejection_reason,
            "rejected_at": updated_business.rejected_at
        }

    except HTTPException:
        raise

    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Service Error: {str(e)}"
        )

# SUSPEND BUSINESS
def suspend_business_service(db: Session, business_id, reason: str = None):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )

        if business.status == "suspended":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Business already suspended"
            )

        if business.status == "rejected":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Rejected business cannot be suspended"
            )

        updated_business = suspend_business(db, business, reason)

        return {
            "id": updated_business.id,
            "status": updated_business.status,
            "suspension_reason": updated_business.suspension_reason,
            "suspended_at": updated_business.suspended_at
        }

    except HTTPException:
        raise

    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Service Error: {str(e)}"
        )

# REACTIVATE BUSINESS
def reactivate_business_service(db: Session, business_id):
    try:
        business = get_business_by_id(db, business_id)

        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )

        if business.status != "suspended":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Only suspended businesses can be reactivated"
            )

        updated_business = reactivate_business(db, business)

        return updated_business

    except HTTPException:
        raise

    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Service Error: {str(e)}"
        )

# GET MERCHANT FROM BUSINESS  
def get_associated_merchant_service(db: Session, business_id):
    try:
        business = get_business_with_merchant(db, business_id)

        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )

        if not business.merchant:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "No merchant associated with this business"
            )

        merchant = business.merchant

        #  MAP FIELDS CORRECTLY
        return {
            "id": merchant.id,
            "full_name": merchant.businessName,
            "business_email": merchant.businessEmail,
            "status": merchant.status,
            "created_at": merchant.createdAt
        }

    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Service Error: {str(e)}"
        )
    
def get_all_listings_service(
    db: Session
):

    try:

        total, listings = get_all_listings_repo(
            db=db
        )

        return {
            "success": True,
            "message": "All listings fetched successfully",
            "total": total,
            "data": listings
        }

    except Exception as e:

        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def approve_listing_service(
    db: Session,
    listingId
):

    try:

        # CHECK LISTING EXISTS
        listing = get_listing_by_id_repo(
            db=db,
            listingId=listingId
        )

        if not listing:

            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )

        # APPROVE LISTING
        approved_listing = approve_listing_repo(
            db=db,
            listing=listing
        )

        return {
            "success": True,
            "message": "Listing approved successfully",
            "data": approved_listing
        }

    except HTTPException as e:
        raise e

    except Exception as e:

        db.rollback()

        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def reject_listing_service(
    db: Session,
    listingId,
    payload
):
    try:
        # CHECK LISTING EXISTS
        listing = get_listing_by_id_repo(
            db=db,
            listingId=listingId
        )
        if not listing:

            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        # REJECT LISTING
        rejected_listing = reject_listing_repo(
            db=db,
            listing=listing,
            reason=payload.reason
        )
        return {
            "success": True,
            "message": "Listing rejected successfully",
            "data": rejected_listing
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def suspend_listing_service(
    db,
    listingId,
    payload
):
    try:
        listing = get_listing_by_id_repo(
            db=db,
            listingId=listingId
        )
        if not listing:
            raise HTTPException(
                http_status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        updated_listing = suspend_listing_repo(
            db=db,
            listing=listing,
            reason=payload.reason
        )
        return {
            "success": True,
            "message": "Listing suspended successfully",
            "data": updated_listing
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
def reactivate_listing_service(
    db,
    listingId
):
    try:
        listing = get_listing_by_id_repo(
            db=db,
            listingId=listingId
        )
        if not listing:
            raise HTTPException(
                http_status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        updated_listing = reactivate_listing_repo(
            db=db,
            listing=listing
        )
        return {
            "success": True,
            "message": "Listing reactivated successfully",
            "data": updated_listing
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# CREATE CATEGORY
def create_category_service(
    db,
    payload
):

    try:

        # CHECK CATEGORY EXISTS
        existing_category = get_category_by_id_repo(
            db=db,
            id=payload.categoryId
        )

        if existing_category:

            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Category already exists"
            )

        # CREATE CATEGORY
        category = create_category_repo(
            db=db,
            payload=payload
        )

        return {
            "success": True,
            "message": "Category created successfully",
            "data": category
        }

    except CustomException as e:
        raise e

    except Exception as e:

        db.rollback()

        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )