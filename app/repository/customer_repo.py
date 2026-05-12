from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.customer_model import Customer
from app.models.merchant_model import Merchant, MerchantListing
from app.models.admin_model import Admin
from app.models.category_model import Category

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


def get_all_users(
    db: Session,
    search: str = None,
    role: str = None,
    status: str = None,
    page: int = 1,
    limit: int = 10
):
    users = []

    role = role.lower() if role else None
    status = status.lower() if status else None

    # CUSTOMER USERS
    if role in [None, "", "customer"]:

        query = db.query(Customer)

        if search:
            query = query.filter(
                or_(
                    Customer.firstName.ilike(f"%{search}%"),
                    Customer.lastName.ilike(f"%{search}%"),
                    Customer.email.ilike(f"%{search}%")
                )
            )

        if status:
            query = query.filter(Customer.status == status)

        customers = query.all()

        for row in customers:
            users.append({
                "id": row.id,
                "name": f"{row.firstName} {row.lastName}",
                "email": row.email,
                "role": "customer",
                "status": row.status,
                "createdAt": row.createdAt
            })

    # MERCHANT USERS
    if role in [None, "", "merchant"]:

        query = db.query(Merchant)

        if search:
            query = query.filter(
                or_(
                    Merchant.businessName.ilike(f"%{search}%"),
                    Merchant.businessEmail.ilike(f"%{search}%")
                )
            )

        if status:
            query = query.filter(Merchant.status == status)

        merchants = query.all()

        for row in merchants:
            users.append({
                "id": row.id,
                "name": row.businessName,
                "email": row.businessEmail,
                "role": "merchant",
                "status": row.status,
                "createdAt": row.createdAt
            })

    # ADMIN USERS
    if role in [None, "", "admin"]:

        query = db.query(Admin)

        if search:
            query = query.filter(
                or_(
                    Admin.name.ilike(f"%{search}%"),
                    Admin.email.ilike(f"%{search}%")
                )
            )

        if status:
            query = query.filter(Admin.status == status)

        admins = query.all()

        for row in admins:
            users.append({
                "id": row.id,
                "name": row.name,
                "email": row.email,
                "role": "admin",
                "status": row.status,
                "createdAt": row.createdAt
            })

    users = sorted(
        users,
        key=lambda x: x["createdAt"],
        reverse=True
    )

    total = len(users)

    # PAGINATION
    start = (page - 1) * limit
    end = start + limit

    paginated_users = users[start:end]

    return total, paginated_users

# GET PUBLIC LISTINGS
def get_public_listings_repo(
    db: Session,
    search,
    category,
    listingType,
    city,
    priceMin,
    priceMax,
    skip,
    limit,
    sortBy
):

    query = db.query(MerchantListing).filter(
        MerchantListing.status == "published"
    )

    # SEARCH
    if search:
        query = query.filter(
            or_(
                MerchantListing.title.ilike(f"%{search}%"),
                MerchantListing.description.ilike(f"%{search}%")
            )
        )

    # CATEGORY FILTER
    # category query param will contain categoryId
    if category:
        query = query.filter(
            MerchantListing.categoryId == category
        )

    # LISTING TYPE
    if listingType:
        query = query.filter(
            MerchantListing.listingType == listingType
        )

    # CITY
    if city:
        query = query.filter(
            MerchantListing.location.ilike(f"%{city}%")
        )

    # PRICE MIN
    if priceMin is not None:
        query = query.filter(
            MerchantListing.price >= priceMin
        )

    # PRICE MAX
    if priceMax is not None:
        query = query.filter(
            MerchantListing.price <= priceMax
        )

    # SORTING
    if sortBy == "priceLowToHigh":

        query = query.order_by(
            MerchantListing.price.asc()
        )

    elif sortBy == "priceHighToLow":

        query = query.order_by(
            MerchantListing.price.desc()
        )

    else:

        query = query.order_by(
            MerchantListing.created_at.desc()
        )

    total = query.count()

    listings = query.offset(skip).limit(limit).all()

    return total, listings

def get_public_listing_details_repo(
    db: Session,
    listingId
):

    listing = db.query(MerchantListing).filter(
        MerchantListing.id == listingId,
        MerchantListing.status == "published"
    ).first()

    return listing

def search_listings_repo(
    db: Session,
    keyword,
    category,
    listingType,
    location,
    rating,
    sort
):

    query = db.query(MerchantListing).filter(
        MerchantListing.status == "published"
    )

    # KEYWORD SEARCH
    if keyword:
        query = query.filter(
            or_(
                MerchantListing.title.ilike(f"%{keyword}%"),
                MerchantListing.description.ilike(f"%{keyword}%")
            )
        )

    # CATEGORY
    if category:
        query = query.filter(
            MerchantListing.categoryId == category
        )

    # LISTING TYPE
    if listingType:
        query = query.filter(
            MerchantListing.listingType == listingType
        )

    # LOCATION
    if location:
        query = query.filter(
            MerchantListing.location.ilike(f"%{location}%")
        )

    # SORTING
    if sort == "priceLowToHigh":

        query = query.order_by(
            MerchantListing.price.asc()
        )

    elif sort == "priceHighToLow":

        query = query.order_by(
            MerchantListing.price.desc()
        )

    else:

        query = query.order_by(
            MerchantListing.created_at.desc()
        )

    total = query.count()

    listings = query.all()

    return total, listings

def get_categories_repo(db):

    categories = db.query(Category).filter(
       Category.isActive == True,
        Category.isDeleted == False
    ).order_by(Category.createdAt.desc()).all()

    total = len(categories)

    return total, categories

# GET SUBCATEGORIES
def get_subcategories_repo(
    db: Session,
    categoryId
):

    return db.query(Category).filter(
        Category.parentCategoryId == categoryId,
        Category.isDeleted == False,
        Category.isActive == True
    ).all()