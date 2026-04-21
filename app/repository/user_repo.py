from app.db.database import get_database
from bson import ObjectId

def get_collection():
    db = get_database()
    return db["users"]


def create_user_repo(user: dict):
    collection = get_collection()
    result = collection.insert_one(user)
    return collection.find_one({"_id": result.inserted_id})


def get_user_by_email(email: str):
    collection = get_collection()
    return collection.find_one({"email": email})


def get_user_by_id(user_id: str):
    collection = get_collection()
    return collection.find_one({"_id": ObjectId(user_id)})