# app/services/admin_service.py
from fastapi import status as http_status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.repository.admin_repo import (
    get_admin_by_id,
    get_admin_by_email,
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
    get_category_by_name_repo,
    create_attribute_repo,
    get_attribute_by_slug_repo,
    get_all_attributes_repo,
    get_attribute_by_id_repo,
    update_attribute_repo,
    delete_attribute_repo
)
from app.models.customer_model import Customer
from app.models.merchant_model import Merchant
from app.models.admin_model import Admin
from app.repository.customer_repo import get_all_users
from app.exceptions.custom_exception import CustomException
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.services.email_service import send_email
from app.core.token_blacklist import TOKEN_BLACKLIST
from datetime import datetime, timedelta
import secrets

def admin_login_service(db: Session, payload):
    admin = get_admin_by_email(db, payload.email)
    if not admin:
        raise CustomException(http_status.HTTP_404_NOT_FOUND, "Admin not found")
    if not verify_password(payload.password, admin.password):
        raise CustomException(http_status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    token_data = {"id": str(admin.id), "email": admin.email, "role": "admin"}
    return {
        "success": True,
        "message": "Login successful",
        "accessToken": create_access_token(token_data),
        "refreshToken": create_refresh_token(token_data),
        "tokenType": "bearer"
    }

def forgot_password_admin_service(db: Session, email: str):
    admin = get_admin_by_email(db, email)
    if not admin:
        raise CustomException(http_status.HTTP_404_NOT_FOUND, "Admin not found")
    reset_token = secrets.token_urlsafe(32)
    admin.resetToken = reset_token
    admin.resetTokenExpiry = datetime.utcnow() + timedelta(minutes=15)
    db.commit()
    reset_link = f"http://localhost:8000/reset-password?token={reset_token}"
    if not send_email(admin.email, reset_link):
        raise CustomException(500, "Failed to send email")
    return {"success": True, "message": "Reset link sent successfully"}

def reset_password_admin_service(db: Session, resetToken: str, newPassword: str, confirmPassword: str):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")
    admin = db.query(Admin).filter(Admin.resetToken == resetToken).first()
    if not admin:
        raise CustomException(400, "Invalid or expired token")
    if admin.resetTokenExpiry < datetime.utcnow():
        raise CustomException(400, "Token expired")
    admin.password = hash_password(newPassword)
    admin.resetToken = None
    admin.resetTokenExpiry = None
    db.commit()
    return {"success": True, "message": "Password reset successful"}

def change_password_admin_service(db: Session, admin_id, currentPassword: str, newPassword: str, confirmPassword: str):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")
    admin = get_admin_by_id(db, admin_id)
    if not admin:
        raise CustomException(404, "Admin not found")
    if not verify_password(currentPassword, admin.password):
        raise CustomException(401, "Incorrect current password")
    admin.password = hash_password(newPassword)
    db.commit()
    return {"success": True, "message": "Password changed successfully"}

def logout_admin_service(token: str, current_user):
    TOKEN_BLACKLIST.add(token)
    return {"success": True, "message": "Logged out successfully"}

# ADMIN GET MERCHANTS
def admin_get_merchants_service(
    db: Session,
    search,
    status,
    page,
    limit
):
    try:
        from app.models.merchant_model import Merchant
        from sqlalchemy import or_
        query = db.query(Merchant)
        if search:
            query = query.filter(
                or_(
                    Merchant.fullName.ilike(f"%{search}%"),
                    Merchant.businessEmail.ilike(f"%{search}%"),
                    Merchant.businessName.ilike(f"%{search}%")
                )
            )
        if status:
            query = query.filter(Merchant.status == status.lower())
        total = query.count()
        skip = (page - 1) * limit
        merchants = query.order_by(Merchant.createdAt.desc()).offset(skip).limit(limit).all()
        return {
            "success": True,
            "total": total,
            "page": page,
            "limit": limit,
            "data": [{
                "id": m.id,
                "full_name": m.fullName,
                "business_email": m.businessEmail,
                "status": m.status,
                "created_at": m.createdAt
            } for m in merchants]
        }
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# ADMIN GET MERCHANT DETAILS
def admin_get_merchant_details_service(db: Session, merchant_id):
    try:
        from app.models.merchant_model import Merchant
        merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
        if not merchant:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Merchant not found")
        return {
            "success": True,
            "data": {
                "id": merchant.id,
                "full_name": merchant.fullName,
                "business_email": merchant.businessEmail,
                "mobile_number": merchant.mobileNumber,
                "status": merchant.status,
                "created_at": merchant.createdAt
            }
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# GET ASSOCIATED MERCHANT
def get_associated_merchant_service(db: Session, business_id):
    try:
        business = get_business_with_merchant(db=db, business_id=business_id)
        if not business:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Business not found")
        m = business.merchant
        return {
            "success": True,
            "data": {
                "id": m.id,
                "full_name": m.fullName,
                "business_email": m.businessEmail,
                "status": m.status,
                "created_at": m.createdAt
            }
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# GET ALL LISTINGS
def get_all_listings_service(db: Session):
    try:
        total, listings = get_all_listings_repo(db=db)
        return {"success": True, "message": "Listings fetched", "total": total, "data": listings}
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# APPROVE LISTING
def approve_listing_service(db: Session, listingId):
    try:
        listing = get_listing_by_id_repo(db=db, listingId=listingId)
        if not listing:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Listing not found")
        return {"success": True, "message": "Listing approved", "data": approve_listing_repo(db, listing)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# REJECT LISTING
def reject_listing_service(db: Session, listingId, payload):
    try:
        listing = get_listing_by_id_repo(db=db, listingId=listingId)
        if not listing:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Listing not found")
        return {"success": True, "message": "Listing rejected", "data": reject_listing_repo(db, listing, payload.reason)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# SUSPEND LISTING
def suspend_listing_service(db: Session, listingId, payload):
    try:
        listing = get_listing_by_id_repo(db=db, listingId=listingId)
        if not listing:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Listing not found")
        return {"success": True, "message": "Listing suspended", "data": suspend_listing_repo(db, listing, payload.reason)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# REACTIVATE LISTING
def reactivate_listing_service(db: Session, listingId):
    try:
        listing = get_listing_by_id_repo(db=db, listingId=listingId)
        if not listing:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Listing not found")
        return {"success": True, "message": "Listing reactivated", "data": reactivate_listing_repo(db, listing)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# SUSPEND BUSINESS
def suspend_business_service(db: Session, business_id, reason=None):
    try:
        business = get_business_by_id(db=db, business_id=business_id)
        if not business:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Business not found")
        return {"success": True, "message": "Business suspended", "data": suspend_business(db=db, business=business, reason=reason)}
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# REACTIVATE BUSINESS
def reactivate_business_service(db: Session, business_id):
    try:
        business = get_business_by_id(db=db, business_id=business_id)
        if not business:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Business not found")
        return {"success": True, "message": "Business reactivated", "data": reactivate_business(db=db, business=business)}
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

def get_admin_profile_service(
    db: Session,
    admin_id
):
    try:
        admin = get_admin_by_id(db=db, admin_id=admin_id)
        if not admin:
            raise CustomException(http_status.HTTP_404_NOT_FOUND, "Admin not found")
        return {"success": True, "data": {"id": admin.id, "name": admin.name, "email": admin.email}}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# UPDATE ADMIN PROFILE
def update_admin_profile_service(
    db: Session,
    admin_id,
    payload
):
    try:
        admin = get_admin_by_id(
            db=db,
            admin_id=admin_id
        )
        if not admin:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Admin not found"
            )
        existing_email = get_admin_by_email(
            db=db,
            email=payload.email
        )
        if existing_email and existing_email.id != admin.id:
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Email already exists"
            )
        admin.name = payload.name
        admin.email = payload.email
        update_admin(
            db=db,
            admin=admin
        )
        return {
            "success": True,
            "message": "Profile updated successfully",
            "data": {
                "id": admin.id,
                "name": admin.name,
                "email": admin.email
            }
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# ADMIN GET USERS
def admin_get_users_service(
    db: Session,
    search,
    role,
    status,
    page,
    limit
):
    try:
        total, users = get_all_users(
            db=db,
            search=search,
            role=role,
            status=status,
            page=page,
            limit=limit
        )
        return {
            "success": True,
            "total": total,
            "page": page,
            "limit": limit,
            "data": users
        }
    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# ADMIN GET USER DETAILS
def admin_get_user_details_service(
    db: Session,
    user_id
):
    try:
        customer = db.query(Customer).filter(
            Customer.id == user_id
        ).first()
        if customer:
            return {
                "success": True,
                "data": {
                    "id": customer.id,
                    "name": f"{customer.firstName} {customer.lastName}",
                    "email": customer.email,
                    "role": "customer",
                    "status": customer.status,
                    "createdAt": customer.createdAt
                }
            }
        merchant = db.query(Merchant).filter(
            Merchant.id == user_id
        ).first()
        if merchant:
            return {
                "success": True,
                "data": {
                    "id": merchant.id,
                    "name": merchant.businessName,
                    "email": merchant.businessEmail,
                    "role": "merchant",
                    "status": merchant.status,
                    "createdAt": merchant.createdAt
                }
            }
        admin = db.query(Admin).filter(
            Admin.id == user_id
        ).first()
        if admin:
            return {
                "success": True,
                "data": {
                    "id": admin.id,
                    "name": admin.name,
                    "email": admin.email,
                    "role": "admin",
                    "status": admin.status,
                    "createdAt": admin.createdAt
                }
            }
        raise CustomException(
            http_status.HTTP_404_NOT_FOUND,
            "User not found"
        )
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# ADMIN UPDATE USER STATUS
def admin_update_user_status_service(
    db: Session,
    user_id,
    payload
):
    try:
        new_status = payload.status.lower()
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
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# MERCHANT MANAGEMENT (ADMIN)
# GET MERCHANTS LIST
def admin_get_merchants_service(
    db: Session,
    search=None,
    status=None,
    page: int = 1,
    limit: int = 10
):
    try:
        query = db.query(Merchant)
        # SEARCH
        if search:
            query = query.filter(
                or_(
                    Merchant.fullName.ilike(f"%{search}%"),
                    Merchant.businessName.ilike(f"%{search}%"),
                    Merchant.businessEmail.ilike(f"%{search}%")
                )
            )
        # STATUS FILTER
        if status:
            query = query.filter(
                Merchant.status == status
            )
        total = query.count()
        merchants = query.order_by(
            Merchant.createdAt.desc()
        ).offset(
            (page - 1) * limit
        ).limit(limit).all()
        result = []
        for merchant in merchants:
            result.append({
                "id": merchant.id,
                "fullName": merchant.fullName,
                "businessName": merchant.businessName,
                "businessEmail": merchant.businessEmail,
                "mobileNumber": merchant.mobileNumber,
                "status": merchant.status,
                "createdAt": merchant.createdAt
            })
        return {
            "success": True,
            "message": "Merchants fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": result
        }
    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# GET MERCHANT DETAILS
def admin_get_merchant_details_service(
    db: Session,
    merchant_id
):
    try:
        merchant = db.query(Merchant).filter(
            Merchant.id == merchant_id
        ).first()
        if not merchant:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Merchant not found"
            )
        return {
            "success": True,
            "message": "Merchant details fetched successfully",
            "data": {
                "id": merchant.id,
                "fullName": merchant.fullName,
                "businessName": merchant.businessName,
                "businessEmail": merchant.businessEmail,
                "mobileNumber": merchant.mobileNumber,
                "status": merchant.status,
                "createdAt": merchant.createdAt
            }
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

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
        result = []
        for business in businesses:
            result.append({
                "id": business.id,
                "name": business.name,
                "category": business.category,
                "status": business.status,
                "created_at": business.created_at
            })
        return {
            "success": True,
            "total": total,
            "page": page,
            "limit": limit,
            "data": result
        }
    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# FETCH BUSINESS DETAIL
def fetch_business_detail_service(
    db: Session,
    business_id
):
    try:
        business = get_business_by_id(
            db=db,
            business_id=business_id
        )
        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )
        return {
            "success": True,
            "data": {
                "id": business.id,
                "name": business.name,
                "category": business.category,
                "status": business.status,
                "created_at": business.created_at
            }
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# APPROVE BUSINESS
def approve_business_service(
    db: Session,
    business_id
):
    try:
        business = get_business_by_id(
            db=db,
            business_id=business_id
        )
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
        updated_business = approve_business(
            db=db,
            business=business
        )
        return {
            "success": True,
            "message": "Business approved successfully",
            "data": updated_business
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# REJECT BUSINESS
def reject_business_service(
    db: Session,
    business_id,
    reason=None
):
    try:
        business = get_business_by_id(
            db=db,
            business_id=business_id
        )
        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )
        updated_business = reject_business(
            db=db,
            business=business,
            reason=reason
        )
        return {
            "success": True,
            "message": "Business rejected successfully",
            "data": updated_business
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# SUSPEND BUSINESS
def suspend_business_service(
    db: Session,
    business_id,
    reason=None
):
    try:
        business = get_business_by_id(
            db=db,
            business_id=business_id
        )
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
        updated_business = suspend_business(
            db=db,
            business=business,
            reason=reason
        )
        return {
            "success": True,
            "message": "Business suspended successfully",
            "data": {
                "id": updated_business.id,
                "status": updated_business.status,
                "reason": updated_business.suspension_reason,
                "suspendedAt": updated_business.suspended_at
            }
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# REACTIVATE BUSINESS
def reactivate_business_service(
    db: Session,
    business_id
):
    try:
        business = get_business_by_id(
            db=db,
            business_id=business_id
        )
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
        updated_business = reactivate_business(
            db=db,
            business=business
        )
        return {
            "success": True,
            "message": "Business reactivated successfully",
            "data": {
                "id": updated_business.id,
                "status": updated_business.status
            }
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# GET ASSOCIATED MERCHANT
def get_associated_merchant_service(
    db: Session,
    business_id
):
    try:
        business = get_business_with_merchant(
            db=db,
            business_id=business_id
        )
        if not business:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Business not found"
            )
        if not business.merchant:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Associated merchant not found"
            )
        merchant = business.merchant
        return {
            "success": True,
            "message": "Associated merchant fetched successfully",
            "data": {
                "id": merchant.id,
                "fullName": merchant.fullName,
                "businessName": merchant.businessName,
                "businessEmail": merchant.businessEmail,
                "mobileNumber": merchant.mobileNumber,
                "status": merchant.status,
                "createdAt": merchant.createdAt
            }
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# CREATE CATEGORY
def create_category_service(
    db: Session,
    payload
):
    try:
        existing_category = get_category_by_name_repo(
            db=db,
            name=payload.name
        )
        if existing_category:

            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Category already exists"
            )
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

# LISTING MANAGEMENT (ADMIN)
def get_all_listings_service(
    db: Session
):
    try:
        total, listings = get_all_listings_repo(db=db)
        return {
            "success": True,
            "message": "Listings fetched successfully",
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
        listing = get_listing_by_id_repo(db=db, listingId=listingId)
        if not listing:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        if listing.status == "approved":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Listing already approved"
            )
        updated = approve_listing_repo(db=db, listing=listing)
        return {
            "success": True,
            "message": "Listing approved successfully",
            "data": updated
        }
    except CustomException:
        raise
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
        listing = get_listing_by_id_repo(db=db, listingId=listingId)
        if not listing:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        if listing.status == "rejected":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Listing already rejected"
            )
        updated = reject_listing_repo(
            db=db,
            listing=listing,
            reason=payload.reason
        )
        return {
            "success": True,
            "message": "Listing rejected successfully",
            "data": updated
        }
    except CustomException:
        raise
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )


def suspend_listing_service(
    db: Session,
    listingId,
    payload
):
    try:
        listing = get_listing_by_id_repo(db=db, listingId=listingId)

        if not listing:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        if listing.status == "suspended":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Listing already suspended"
            )
        updated = suspend_listing_repo(
            db=db,
            listing=listing,
            reason=payload.reason
        )
        return {
            "success": True,
            "message": "Listing suspended successfully",
            "data": updated
        }
    except CustomException:
        raise
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def reactivate_listing_service(
    db: Session,
    listingId
):
    try:
        listing = get_listing_by_id_repo(db=db, listingId=listingId)
        if not listing:
            raise CustomException(
                http_status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        if listing.status != "suspended":
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Only suspended listings can be reactivated"
            )
        updated = reactivate_listing_repo(db=db, listing=listing)
        return {
            "success": True,
            "message": "Listing reactivated successfully",
            "data": updated
        }
    except CustomException:
        raise
    except Exception as e:
        db.rollback()
        raise CustomException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

# CREATE ATTRIBUTE
def create_attribute_service(
    db: Session,
    payload
):
    existing_attribute = get_attribute_by_slug_repo(
        db,
        payload.slug
    )
    if existing_attribute:
        raise CustomException(
            http_status.HTTP_400_BAD_REQUEST,
            "Attribute slug already exists"
        )
    # DROPDOWN VALIDATION
    if payload.field_type == "dropdown":
        if not payload.options:
            raise CustomException(
                http_status.HTTP_400_BAD_REQUEST,
                "Dropdown field requires options"
            )
    return create_attribute_repo(
        db,
        payload
    )

# GET ALL ATTRIBUTES
def get_all_attributes_service(db: Session):
    return get_all_attributes_repo(db)

# GET ATTRIBUTE BY ID
def get_attribute_by_id_service(
    db: Session,
    attribute_id
):
    attribute = get_attribute_by_id_repo(
        db,
        attribute_id
    )
    if not attribute:
        raise CustomException(
            http_status.HTTP_404_NOT_FOUND,
            "Attribute not found"
        )
    return attribute

# UPDATE ATTRIBUTE
def update_attribute_service(
    db: Session,
    attribute_id,
    payload
):
    attribute = get_attribute_by_id_repo(
        db,
        attribute_id
    )
    if not attribute:
        raise CustomException(
            http_status.HTTP_404_NOT_FOUND,
            "Attribute not found"
        )
    return update_attribute_repo(
        db,
        attribute,
        payload
    )

# DELETE ATTRIBUTE
def delete_attribute_service(
    db: Session,
    attribute_id
):
    attribute = get_attribute_by_id_repo(
        db,
        attribute_id
    )
    if not attribute:
        raise CustomException(
            http_status.HTTP_404_NOT_FOUND,
            "Attribute not found"
        )
    delete_attribute_repo(
        db,
        attribute
    )
    return {
        "message": "Attribute deleted successfully"
    }