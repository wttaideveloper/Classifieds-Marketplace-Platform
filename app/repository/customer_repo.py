from app.db.database import get_database
from bson import ObjectId

def get_collection():
    db = get_database()
    return db["customers"]

def create_customer_repo(user: dict):
    collection = get_collection()
    result = collection.insert_one(user)
    return collection.find_one({"_id": result.inserted_id})


def get_customer_by_email(email: str):
    collection = get_collection()
    return collection.find_one({"email": email})


def get_customer_by_id(cust_id: str):
    collection = get_collection()
    return collection.find_one({"_id": ObjectId(cust_id)})

def update_customer_repo(cust_id, data):
    collection = get_collection()
    return collection.update_one(
        {"_id": ObjectId(cust_id)},
        {"$set": data}
    )

def get_customer_by_reset_token(token):
    collection = get_collection()
    return collection.find_one({"resetToken": token})

def update_customer_profile(email: str, data: dict):
    collection = get_collection()
    return collection.update_one(
        {"email": email},
        {"$set": data}
    )