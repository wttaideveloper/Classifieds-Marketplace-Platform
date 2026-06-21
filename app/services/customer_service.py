from fastapi import status
from sqlalchemy.orm import Session
from app.repository.customer_repo import *
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.exceptions.custom_exception import CustomException
from app.models.customer_model import Customer
from app.services.email_service import send_email  
from app.core.token_blacklist import TOKEN_BLACKLIST
from uuid import UUID, uuid4
from datetime import date, datetime, timedelta
import json
import requests
import secrets
from app.core.token_blacklist import TOKEN_BLACKLIST
from app.services.common_service import (
    CustomException
)
from app.schemas.common_schema import (
    CreateBooking
)

GOOGLE_VERIFY_URL = "https://oauth2.googleapis.com/tokeninfo"

# REGISTER
def register_customer_service(db, customer):
    existing = get_customer_by_email(db, customer.email)
    if existing:
        raise CustomException(400, "Email already registered")
    if customer.password != customer.confirmPassword:
        raise CustomException(400, "Passwords do not match")
    if not customer.acceptTerms or not customer.acceptPrivacyPolicy:
        raise CustomException(400, "Accept terms and privacy policy")
    data = customer.dict()
    data.pop("confirmPassword")
    data["password"] = hash_password(data["password"])
    new_customer = Customer(**data)
    return create_customer(db, new_customer)

# LOGIN
def login_customer_service(db, email, password):

    user = get_customer_by_email(db, email)

    if not user:
        raise CustomException(404, "User not found")

    if not verify_password(password, user.password):
        raise CustomException(401, "Invalid credentials")

    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "role": "customer"
    }

    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    return {
        "success": True,
        "message": "Login successful",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

# GOOGLE LOGIN
def google_login_service(db, google_token: str):
    res = requests.get(GOOGLE_VERIFY_URL, params={"id_token": google_token})
    if res.status_code != 200:
        raise CustomException(401, "Invalid Google token")
    data = res.json()
    email = data.get("email")
    name = data.get("name", "")
    user = get_customer_by_email(db, email)
    if not user:
        user_data = Customer(
            id=str(uuid4()),
            firstName=name,
            lastName="",
            email=email,
            mobileNumber="",
            password=hash_password(secrets.token_urlsafe(16)),  # safe placeholder
            acceptTerms=True,
            acceptPrivacyPolicy=True
        )
        user = create_customer(db, user_data)
    token = create_access_token({
        "sub": user.id,
        "email": user.email
    })
    return {
        "access_token": token,
        "token_type": "bearer"
    }

# FORGOT PASSWORD 
def forgot_password_service(db, email: str):
    user = get_customer_by_email(db, email)
    if not user:
        raise CustomException(404, "User not found")
    reset_token = secrets.token_urlsafe(32)
    expiry = datetime.utcnow() + timedelta(minutes=15)
    user.resetToken = reset_token
    user.resetTokenExpiry = expiry
    db.commit()
    #  CREATE RESET LINK
    reset_link = f"http://localhost:8000/reset-password?token={reset_token}"
    # SEND EMAIL
    email_sent = send_email(user.email, reset_link)
    if not email_sent:
        raise CustomException(500, "Failed to send email")
    return {"message": "Reset link sent successfully"}

# RESET PASSWORD
def reset_password_service(db, resetToken, newPassword, confirmPassword):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")
    user = db.query(Customer).filter(Customer.resetToken == resetToken).first()
    if not user:
        raise CustomException(404, "Invalid token")
    if user.resetTokenExpiry < datetime.utcnow():
        raise CustomException(400, "Token expired")
    user.password = hash_password(newPassword)
    user.resetToken = None
    user.resetTokenExpiry = None
    db.commit()
    return {"message": "Password reset successful"}

# CHANGE PASSWORD
def change_password_service(db, user_id, currentPassword, newPassword, confirmPassword):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")
    user = get_customer_by_id(db, user_id)
    if not user:
        raise CustomException(404, "User not found")
    if not verify_password(currentPassword, user.password):
        raise CustomException(401, "Incorrect current password")
    user.password = hash_password(newPassword)
    db.commit()
    return {"message": "Password changed successfully"}

# LOGOUT
def logout_customer_service(token: str, current_user):
    TOKEN_BLACKLIST.add(token)
    return {"success": True, "message": "Logged out successfully"}

# GET PROFILE
def get_profile_service(db, cust_id):
    user = get_customer_by_id(db, cust_id)
    if not user:
        raise CustomException(404, "User not found")
    return {
        "id": user.id,
        "firstName": user.firstName,
        "lastName": user.lastName,
        "email": user.email,
        "mobileNumber": user.mobileNumber
    }

# UPDATE PROFILE
def update_profile_service(db, cust_id, data):
    user = get_customer_by_id(db, cust_id)
    if not user:
        raise CustomException(404, "User not found")
    allowed_fields = {
        "firstName",
        "lastName",
        "mobileNumber",
        "profileImage"
    }
    clean_data = {
        k: v for k, v in data.items()
        if k in allowed_fields and v is not None
    }
    for k, v in clean_data.items():
        setattr(user, k, v)
    db.commit()
    db.refresh(user)
    return {
        "message": "Profile updated successfully"
    }


def get_public_listings_service(
    db: Session,
    search,
    category,
    listingType,
    city,
    priceMin,
    priceMax,
    page,
    limit,
    sortBy
):
    try:
        skip = (page - 1) * limit
        total, listings = get_public_listings_repo(
            db=db,
            search=search,
            category=category,
            listingType=listingType,
            city=city,
            priceMin=priceMin,
            priceMax=priceMax,
            skip=skip,
            limit=limit,
            sortBy=sortBy
        )
        return {
            "success": True,
            "message": "Public listings fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": listings
        }
    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
def get_public_listing_details_service(
    db: Session,
    listingId
):
    try:
        listing = get_public_listing_details_repo(
            db=db,
            listingId=listingId
        )
        if not listing:
            raise CustomException(
                status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        return {
            "success": True,
            "message": "Listing details fetched successfully",
            "data": listing
        }
    except CustomException  as e:
        raise e
    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )


def _normalize_enterprise_status(status_value):
    status_value = (status_value or "pending").lower()
    if status_value == "approved":
        return "active"
    if status_value in {"suspended", "rejected", "inactive"}:
        return "inactive"
    if status_value in {"pending", "draft"}:
        return "pending"
    return status_value if status_value in {"active", "inactive", "pending"} else "pending"


def _enterprise_response(business, metrics=None, rating=None):
    metrics = metrics or {}
    merchant = business.merchant
    profile = merchant.business_profile if merchant else None
    profile_extra = {}
    if profile and isinstance(profile.socialMediaLinks, dict):
        profile_extra = profile.socialMediaLinks.get("frontend_fields", {})

    return {
        "id": business.id,
        "name": business.name,
        "businessLegalName": business.name,
        "businessShortName": profile.businessName if profile else None,
        "businessDescription": profile.businessDescription if profile else None,
        "businessEmail": profile.businessEmail if profile else (merchant.businessEmail if merchant else None),
        "businessPhone": profile.phoneNumber if profile else (merchant.mobileNumber if merchant else None),
        "registeredAddress": profile.fullAddress if profile else None,
        "businessAddress": profile_extra.get("business_address"),
        "communicationAddress": profile_extra.get("communication_address"),
        "logoUrl": profile.businessLogo if profile else None,
        "businessImages": profile.galleryImages if profile and profile.galleryImages else [],
        "registrationNumber": profile_extra.get("registration_number"),
        "businessCategory": profile.primaryCategory if profile else business.category,
        "websiteUrl": profile.websiteUrl if profile else None,
        "yearFounded": profile_extra.get("year_founded"),
        "primaryContactName": profile_extra.get("primary_contact_name") or (merchant.fullName if merchant else None),
        "primaryContactTitle": profile_extra.get("primary_contact_title"),
        "secondaryEmail": profile_extra.get("secondary_email"),
        "secondaryPhone": profile_extra.get("secondary_phone"),
        "suiteUnit": profile_extra.get("suite_unit"),
        "brandColor": profile_extra.get("brand_color"),
        "tagline": profile.shortTagline if profile else profile_extra.get("tagline"),
        "category": business.category,
        "status": _normalize_enterprise_status(business.status),
        "membersCount": metrics.get("membersCount", 0),
        "revenue": metrics.get("revenue", 0.0),
        "joinedDate": business.created_at,
        "rating": rating,
    }


def _attribute_value(attributes, *names):
    for name in names:
        value = attributes.get(name.lower().replace(" ", "_"))
        if value is not None:
            return value
    return None


def _to_float(value):
    if value is None:
        return None


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_images(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value] if value else []
    return []
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_jsonish(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _service_availability_response(listing):
    raw_availability = _parse_jsonish(listing.availability)
    raw_schedule = _parse_jsonish(listing.schedule)
    week_dates = [date.today() + timedelta(days=index) for index in range(7)]
    day_wise_slot_count = {}
    slot_timings = []

    if isinstance(raw_schedule, dict):
        for day, slots in raw_schedule.items():
            if isinstance(slots, list):
                day_wise_slot_count[str(day)] = len(slots)
                slot_timings.extend([str(slot) for slot in slots])
            elif slots:
                day_wise_slot_count[str(day)] = 1
                slot_timings.append(str(slots))

    if not slot_timings and isinstance(raw_availability, dict):
        slots = raw_availability.get("slotTimings") or raw_availability.get("slot_timings")
        if isinstance(slots, list):
            slot_timings = [str(slot) for slot in slots]

    return {
        "weekDates": week_dates,
        "dayWiseSlotCount": day_wise_slot_count,
        "slotTimings": slot_timings,
        "rawAvailability": raw_availability,
        "rawSchedule": raw_schedule,
    }


def _listing_base_response(listing, rating=None):
    return {
        "id": listing.id,
        "businessId": listing.businessId,
        "listingType": listing.listingType,
        "title": listing.title,
        "description": listing.description,
        "price": listing.price,
        "currency": listing.currency,
        "images": listing.images or [],
        "status": listing.status,
        "rating": rating,
    }


def _product_response(listing, rating=None, attributes=None):
    attributes = attributes or {}
    data = _listing_base_response(listing, rating)
    data.update({
        "enterpriseName": listing.business.name if listing.business else None,
        "productCategory": _attribute_value(attributes, "product_category"),
        "sku": listing.sku,
        "barcodeUpc": _attribute_value(attributes, "barcode_upc"),
        "weight": listing.weight,
        "dimensions": _attribute_value(attributes, "dimensions"),
        "salePrice": _attribute_value(attributes, "sale_price"),
        "costPrice": _attribute_value(attributes, "cost_price"),
        "taxClass": _attribute_value(attributes, "tax_class"),
        "stockQuantity": listing.stockQuantity,
        "lowStockAlertThreshold": _attribute_value(attributes, "low_stock_alert_threshold"),
        "stockManagement": _attribute_value(attributes, "stock_management"),
        "publishStatus": _attribute_value(attributes, "publish_status") or listing.status,
        "length": _to_float(_attribute_value(attributes, "length")),
        "width": _to_float(_attribute_value(attributes, "width")),
        "thick": _to_float(_attribute_value(attributes, "thick", "thickness")),
        "stockCount": listing.stockQuantity,
    })
    return data


def _service_response(listing, attributes=None, include_details=False):
    attributes = attributes or {}
    business = listing.business
    merchant = business.merchant if business else None
    profile = merchant.business_profile if merchant else None
    availability_schedule = _parse_jsonish(listing.schedule) or []
    if not isinstance(availability_schedule, list):
        availability_schedule = []
    provider_name = _attribute_value(attributes, "provider_name")
    instructor_name = _attribute_value(attributes, "instructor_name")
    trainer_name = instructor_name or provider_name or _attribute_value(attributes, "trainer_name", "trainer") or (
        merchant.fullName if merchant else None
    )

    data = {
        "id": listing.id,
        "businessId": listing.businessId,
        "listingType": listing.listingType,
        "title": listing.title,
        "description": listing.description,
        "price": listing.price,
        "currency": listing.currency,
        "images": listing.images or [],
        "status": listing.status,
        "trainerName": trainer_name,
        "serviceCategory": _attribute_value(attributes, "service_category"),
        "maxParticipants": listing.capacity,
        "providerName": provider_name,
        "instructorName": instructor_name,
        "deliveryFormat": _attribute_value(attributes, "delivery_format") or listing.serviceMode,
        "packagePrice": _attribute_value(attributes, "package_price"),
        "currency": listing.currency,
        "cancellationPolicy": _attribute_value(attributes, "cancellation_policy"),
        "availabilitySchedule": availability_schedule,
    }

    if include_details:
        data.update({
            "bannerImage": profile.bannerImage if profile else ((listing.images or [None])[0]),
            "expertiseName": _attribute_value(attributes, "expertise_name", "expertise"),
            "type": _attribute_value(attributes, "type") or listing.listingType,
            "format": _attribute_value(attributes, "format") or listing.serviceMode,
            "availability": _service_availability_response(listing),
        })

    return data


def _upsert_listing_attributes(db: Session, listing_id, values):
    from app.models.admin_model import Attribute, AttributeFieldType
    from app.models.merchant_model import ListingAttributeMapping

    created_by = UUID("00000000-0000-0000-0000-000000000000")
    for slug, value in values.items():
        if value is None:
            continue

        normalized_slug = slug.strip().lower().replace(" ", "_")
        attribute = db.query(Attribute).filter(Attribute.slug == normalized_slug).first()
        if not attribute:
            attribute = Attribute(
                name=normalized_slug,
                display_name=normalized_slug.replace("_", " ").title(),
                slug=normalized_slug,
                field_type=AttributeFieldType.text,
                is_required=False,
                is_active=True,
                is_global=True,
                created_by=created_by,
            )
            db.add(attribute)
            db.flush()

        mapping = (
            db.query(ListingAttributeMapping)
            .filter(
                ListingAttributeMapping.listing_id == listing_id,
                ListingAttributeMapping.attribute_id == attribute.id,
            )
            .first()
        )
        attribute_value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if mapping:
            mapping.attribute_value = attribute_value
        else:
            db.add(ListingAttributeMapping(
                listing_id=listing_id,
                attribute_id=attribute.id,
                attribute_value=attribute_value,
            ))
    db.commit()


def _enterprise_extra_payload(payload_data):
    keys = [
        "business_address",
        "communication_address",
        "registration_number",
        "year_founded",
        "primary_contact_name",
        "primary_contact_title",
        "secondary_email",
        "secondary_phone",
        "suite_unit",
        "brand_color",
        "tagline",
    ]
    return {key: payload_data[key] for key in keys if payload_data.get(key) is not None}


def _apply_enterprise_payload(db: Session, business, merchant, profile, payload_data):
    if payload_data.get("business_legal_name") is not None:
        business.name = payload_data["business_legal_name"]
        merchant.businessName = payload_data["business_legal_name"]
    if payload_data.get("business_short_name") is not None:
        profile.businessName = payload_data["business_short_name"]
    if payload_data.get("business_description") is not None:
        profile.businessDescription = payload_data["business_description"]
    if payload_data.get("business_email") is not None:
        merchant.businessEmail = payload_data["business_email"]
        profile.businessEmail = payload_data["business_email"]
    if payload_data.get("business_phone") is not None:
        merchant.mobileNumber = payload_data["business_phone"]
        profile.phoneNumber = payload_data["business_phone"]
    if payload_data.get("registered_address") is not None:
        profile.fullAddress = payload_data["registered_address"]
    if payload_data.get("business_category") is not None:
        business.category = payload_data["business_category"]
        profile.primaryCategory = payload_data["business_category"]
    if payload_data.get("website_url") is not None:
        profile.websiteUrl = payload_data["website_url"]
    if payload_data.get("logo_url") is not None:
        profile.businessLogo = payload_data["logo_url"]
    if payload_data.get("business_images") is not None:
        profile.galleryImages = _normalize_images(payload_data["business_images"])
    if payload_data.get("tagline") is not None:
        profile.shortTagline = payload_data["tagline"]

    social_links = profile.socialMediaLinks if isinstance(profile.socialMediaLinks, dict) else {}
    extra = social_links.get("frontend_fields", {})
    extra.update(_enterprise_extra_payload(payload_data))
    social_links["frontend_fields"] = extra
    profile.socialMediaLinks = social_links

    db.add_all([business, merchant, profile])
    db.commit()
    db.refresh(business)
    return business


def create_enterprise_service(db: Session, payload):
    from app.models.admin_model import Business
    from app.models.merchant_model import Merchant, MerchantProfile

    try:
        payload_data = payload.model_dump(exclude_unset=True)
        merchant = Merchant(
            fullName=payload_data.get("primary_contact_name") or payload.business_short_name,
            businessEmail=payload.business_email,
            mobileNumber=payload.business_phone,
            businessName=payload.business_legal_name,
            password=hash_password(secrets.token_urlsafe(16)),
            acceptTerms=True,
            acceptPrivacyPolicy=True,
            status="active",
        )
        db.add(merchant)
        db.flush()

        business = Business(
            name=payload.business_legal_name,
            category=payload_data.get("business_category"),
            status="pending",
            merchant_id=merchant.id,
        )
        db.add(business)
        db.flush()

        profile = MerchantProfile(
            id=str(uuid4()),
            merchant_id=merchant.id,
            businessName=payload.business_short_name,
            businessDescription=payload.business_description,
            primaryCategory=payload_data.get("business_category") or "General",
            subcategory=None,
            businessEmail=payload.business_email,
            phoneNumber=payload.business_phone,
            fullAddress=payload.registered_address,
            city="N/A",
            state="N/A",
            zipCode="N/A",
            country="N/A",
            latitude=None,
            longitude=None,
            businessLogo=payload_data.get("logo_url"),
            bannerImage=None,
            galleryImages=_normalize_images(payload_data.get("business_images")),
            operatingHours={},
            businessType="hybrid",
            cancellationPolicy=None,
            refundPolicy=None,
            merchantTermsOfService=None,
            websiteUrl=payload_data.get("website_url"),
            socialMediaLinks={"frontend_fields": _enterprise_extra_payload(payload_data)},
            additionalContactNumbers=[],
            shortTagline=payload_data.get("tagline"),
            status="pending",
        )
        db.add(profile)
        db.commit()
        db.refresh(business)

        return {
            "success": True,
            "message": "Enterprise created successfully",
            "data": _enterprise_response(business),
        }
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_enterprise_service(db: Session, enterprise_id, payload):
    try:
        business = get_enterprise_details_repo(db, enterprise_id)
        if not business:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Enterprise not found")

        merchant = business.merchant
        profile = merchant.business_profile if merchant else None
        if not merchant or not profile:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Enterprise profile not found")

        payload_data = payload.model_dump(exclude_unset=True)
        business = _apply_enterprise_payload(
            db=db,
            business=business,
            merchant=merchant,
            profile=profile,
            payload_data=payload_data,
        )

        return {
            "success": True,
            "message": "Enterprise updated successfully",
            "data": _enterprise_response(business),
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def _product_attribute_payload(payload_data):
    return {
        "product_category": payload_data.get("product_category"),
        "barcode_upc": payload_data.get("barcode_upc"),
        "dimensions": payload_data.get("dimensions"),
        "sale_price": payload_data.get("sale_price"),
        "cost_price": payload_data.get("cost_price"),
        "tax_class": payload_data.get("tax_class"),
        "low_stock_alert_threshold": payload_data.get("low_stock_alert_threshold"),
        "stock_management": payload_data.get("stock_management"),
        "publish_status": payload_data.get("publish_status"),
    }


def _service_attribute_payload(payload_data):
    return {
        "service_category": payload_data.get("service_category"),
        "provider_name": payload_data.get("provider_name"),
        "instructor_name": payload_data.get("instructor_name"),
        "delivery_format": payload_data.get("delivery_format"),
        "package_price": payload_data.get("package_price"),
        "cancellation_policy": payload_data.get("cancellation_policy"),
    }


def create_product_service(db: Session, payload):
    from app.models.admin_model import Business
    from app.models.merchant_model import MerchantListing

    try:
        payload_data = payload.model_dump(exclude_unset=True)
        business = db.query(Business).filter(Business.id == payload.enterprise_id).first()
        if not business:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Enterprise not found")

        listing = MerchantListing(
            businessId=payload.enterprise_id,
            listingType="product",
            title=payload.product_name,
            description=payload.product_description,
            categoryId=None,
            subcategoryId=None,
            price=_to_float(payload.product_price),
            currency=payload_data.get("currency") or "INR",
            images=_normalize_images(payload_data.get("product_images")),
            status=payload_data.get("publish_status") or ("published" if payload.product_status else "draft"),
            tags=[],
            stockQuantity=_to_int(payload_data.get("stock_quantity")),
            sku=payload_data.get("sku"),
            weight=_to_float(payload_data.get("weight")),
        )
        db.add(listing)
        db.commit()
        db.refresh(listing)
        _upsert_listing_attributes(db, listing.id, _product_attribute_payload(payload_data))
        attributes = get_listing_attributes_repo(db, [listing.id])

        return {
            "success": True,
            "message": "Product created successfully",
            "data": _product_response(listing, attributes=attributes.get(listing.id)),
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_product_service(db: Session, product_id, payload):
    from app.models.admin_model import Business

    try:
        product = get_public_listing_details_by_type_repo(db, product_id, "product")
        if not product:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Product not found")

        payload_data = payload.model_dump(exclude_unset=True)
        if payload_data.get("enterprise_id") is not None:
            business = db.query(Business).filter(Business.id == payload_data["enterprise_id"]).first()
            if not business:
                raise CustomException(status.HTTP_404_NOT_FOUND, "Enterprise not found")
            product.businessId = payload_data["enterprise_id"]
        if payload_data.get("product_name") is not None:
            product.title = payload_data["product_name"]
        if payload_data.get("product_description") is not None:
            product.description = payload_data["product_description"]
        if payload_data.get("product_price") is not None:
            product.price = _to_float(payload_data["product_price"])
        if payload_data.get("currency") is not None:
            product.currency = payload_data["currency"]
        if payload_data.get("product_images") is not None:
            product.images = _normalize_images(payload_data["product_images"])
        if payload_data.get("product_status") is not None:
            product.status = "published" if payload_data["product_status"] else "draft"
        if payload_data.get("publish_status") is not None:
            product.status = payload_data["publish_status"]
        if payload_data.get("stock_quantity") is not None:
            product.stockQuantity = _to_int(payload_data["stock_quantity"])
        if payload_data.get("sku") is not None:
            product.sku = payload_data["sku"]
        if payload_data.get("weight") is not None:
            product.weight = _to_float(payload_data["weight"])

        db.add(product)
        db.commit()
        db.refresh(product)
        _upsert_listing_attributes(db, product.id, _product_attribute_payload(payload_data))
        attributes = get_listing_attributes_repo(db, [product.id])

        return {
            "success": True,
            "message": "Product updated successfully",
            "data": _product_response(product, attributes=attributes.get(product.id)),
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def create_service_service(db: Session, payload):
    from app.models.admin_model import Business
    from app.models.merchant_model import MerchantListing

    try:
        payload_data = payload.model_dump(exclude_unset=True)
        business = db.query(Business).filter(Business.id == payload.enterprise_id).first()
        if not business:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Enterprise not found")

        schedule = [
            item.model_dump(exclude_none=True)
            for item in (payload.availability_schedule or [])
        ]
        listing = MerchantListing(
            businessId=payload.enterprise_id,
            listingType="service",
            title=payload.service_name,
            description=payload.service_description,
            categoryId=None,
            subcategoryId=None,
            price=_to_float(payload.service_price),
            currency=payload_data.get("currency") or "INR",
            images=[],
            status="published" if payload.service_status else "draft",
            tags=[],
            duration=payload.duration,
            serviceMode=payload_data.get("delivery_format"),
            availability=json.dumps({"availability_status": payload.availability_status}),
            schedule=json.dumps(schedule),
            capacity=_to_int(payload_data.get("max_participants")),
        )
        db.add(listing)
        db.commit()
        db.refresh(listing)
        _upsert_listing_attributes(db, listing.id, _service_attribute_payload(payload_data))
        attributes = get_listing_attributes_repo(db, [listing.id])

        return {
            "success": True,
            "message": "Service created successfully",
            "data": _service_response(
                listing,
                attributes=attributes.get(listing.id),
                include_details=True,
            ),
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_service_service(db: Session, service_id, payload):
    from app.models.admin_model import Business

    try:
        service = get_public_listing_details_by_type_repo(db, service_id, "service")
        if not service:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Service not found")

        payload_data = payload.model_dump(exclude_unset=True)
        if payload_data.get("enterprise_id") is not None:
            business = db.query(Business).filter(Business.id == payload_data["enterprise_id"]).first()
            if not business:
                raise CustomException(status.HTTP_404_NOT_FOUND, "Enterprise not found")
            service.businessId = payload_data["enterprise_id"]
        if payload_data.get("service_name") is not None:
            service.title = payload_data["service_name"]
        if payload_data.get("service_description") is not None:
            service.description = payload_data["service_description"]
        if payload_data.get("service_price") is not None:
            service.price = _to_float(payload_data["service_price"])
        if payload_data.get("currency") is not None:
            service.currency = payload_data["currency"]
        if payload_data.get("duration") is not None:
            service.duration = payload_data["duration"]
        if payload_data.get("service_status") is not None:
            service.status = "published" if payload_data["service_status"] else "draft"
        if payload_data.get("delivery_format") is not None:
            service.serviceMode = payload_data["delivery_format"]
        if payload_data.get("availability_status") is not None:
            service.availability = json.dumps({"availability_status": payload_data["availability_status"]})
        if payload_data.get("availability_schedule") is not None:
            service.schedule = json.dumps([
                item.model_dump(exclude_none=True)
                for item in payload.availability_schedule
            ])
        if payload_data.get("max_participants") is not None:
            service.capacity = _to_int(payload_data["max_participants"])

        db.add(service)
        db.commit()
        db.refresh(service)
        _upsert_listing_attributes(db, service.id, _service_attribute_payload(payload_data))
        attributes = get_listing_attributes_repo(db, [service.id])

        return {
            "success": True,
            "message": "Service updated successfully",
            "data": _service_response(
                service,
                attributes=attributes.get(service.id),
                include_details=True,
            ),
        }
    except CustomException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_enterprises_service(db: Session, page: int, limit: int):
    try:
        skip = (page - 1) * limit
        total, enterprises = get_enterprises_repo(db=db, skip=skip, limit=limit)
        business_ids = [enterprise.id for enterprise in enterprises]
        metrics = get_business_metrics_repo(db, business_ids)
        data = [
            _enterprise_response(
                enterprise,
                metrics=metrics.get(enterprise.id),
            )
            for enterprise in enterprises
        ]
        return {
            "success": True,
            "message": "Enterprises fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": data,
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_enterprise_details_service(db: Session, enterprise_id):
    try:
        enterprise = get_enterprise_details_repo(db, enterprise_id)
        if not enterprise:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Enterprise not found")

        metrics = get_business_metrics_repo(db, [enterprise.id])
        ratings = get_business_ratings_repo(db, [enterprise.id])
        data = _enterprise_response(
            enterprise,
            metrics=metrics.get(enterprise.id),
            rating=ratings.get(enterprise.id),
        )
        return {
            "success": True,
            "message": "Enterprise details fetched successfully",
            "data": data,
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_products_service(db: Session, page: int, limit: int):
    try:
        skip = (page - 1) * limit
        total, products = get_public_listings_by_type_repo(
            db=db,
            listing_type="product",
            skip=skip,
            limit=limit,
        )
        listing_ids = [product.id for product in products]
        ratings = get_listing_ratings_repo(db, listing_ids)
        attributes = get_listing_attributes_repo(db, listing_ids)
        data = [
            _product_response(
                product,
                rating=ratings.get(product.id),
                attributes=attributes.get(product.id),
            )
            for product in products
        ]
        return {
            "success": True,
            "message": "Products fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": data,
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_product_details_service(db: Session, product_id):
    try:
        product = get_public_listing_details_by_type_repo(
            db=db,
            listing_id=product_id,
            listing_type="product",
        )
        if not product:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Product not found")

        ratings = get_listing_ratings_repo(db, [product.id])
        attributes = get_listing_attributes_repo(db, [product.id])
        data = _product_response(
            product,
            rating=ratings.get(product.id),
            attributes=attributes.get(product.id),
        )
        return {
            "success": True,
            "message": "Product details fetched successfully",
            "data": data,
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_services_service(db: Session, page: int, limit: int):
    try:
        skip = (page - 1) * limit
        total, services = get_public_listings_by_type_repo(
            db=db,
            listing_type="service",
            skip=skip,
            limit=limit,
        )
        listing_ids = [service.id for service in services]
        attributes = get_listing_attributes_repo(db, listing_ids)
        data = [
            _service_response(
                service,
                attributes=attributes.get(service.id),
            )
            for service in services
        ]
        return {
            "success": True,
            "message": "Services fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": data,
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def get_service_details_service(db: Session, service_id):
    try:
        service = get_public_listing_details_by_type_repo(
            db=db,
            listing_id=service_id,
            listing_type="service",
        )
        if not service:
            raise CustomException(status.HTTP_404_NOT_FOUND, "Service not found")

        attributes = get_listing_attributes_repo(db, [service.id])
        data = _service_response(
            service,
            attributes=attributes.get(service.id),
            include_details=True,
        )
        return {
            "success": True,
            "message": "Service details fetched successfully",
            "data": data,
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))
    
def search_listings_service(
    db: Session,
    keyword,
    category,
    listingType,
    location,
    rating,
    sort
):
    try:
        total, listings = search_listings_repo(
            db=db,
            keyword=keyword,
            category=category,
            listingType=listingType,
            location=location,
            rating=rating,
            sort=sort
        )
        return {
            "success": True,
            "message": "Listings fetched successfully",
            "total": total,
            "data": listings
        }
    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
def get_categories_service(db):
    try:
        total, categories = get_categories_repo(db)
        return {
            "success": True,
            "message": "Categories fetched successfully",
            "total": total,
            "data": categories
        }
    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
# GET SUBCATEGORIES
def get_subcategories_service(
    db,
    categoryId
):
    try:
        subcategories = get_subcategories_repo(
            db=db,
            categoryId=categoryId
        )
        return {
            "success": True,
            "message": "Subcategories fetched successfully",
            "total": len(subcategories),
            "data": subcategories
        }
    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def create_booking_service(
    db: Session,
    payload: CreateBooking
):
    return create_booking_repo(
        db=db,
        payload=payload
    )

def get_customer_bookings_service(
    db: Session,
    page: int,
    size: int,
    booking_status: str = None
):
    return get_customer_bookings_repo(
        db=db,
        page=page,
        size=size,
        booking_status=booking_status
    )