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
    get_category_by_name_repo
)

from app.models.customer_model import Customer
from app.models.merchant_model import Merchant
from app.models.admin_model import Admin

from app.repository.customer_repo import get_all_users
from app.exceptions.custom_exception import CustomException

# GET ADMIN PROFILE
def get_admin_profile_service(
    db: Session,
    admin_id
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

        return {
            "success": True,
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
                "createdAt": business.createdAt
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
                "createdAt": business.createdAt
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