from pymongo import MongoClient
from app.core.config import settings

class MongoDB:
    client: MongoClient = None

db_instance = MongoDB()


def connect_to_mongo():
    db_instance.client = MongoClient(settings.MONGO_URL)
    print("Connected to MongoDB")


def close_mongo_connection():
    if db_instance.client:
        db_instance.client.close()
        print("MongoDB connection closed")


def get_database():
    return db_instance.client[settings.DATABASE_NAME]