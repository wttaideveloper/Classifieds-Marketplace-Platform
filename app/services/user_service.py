from fastapi import status
from app.repository.user_repo import *
from app.core.security import hash_password, verify_password, create_access_token
from app.exceptions.custom_exception import CustomException
from app.models.user_model import user_helper


def register_user_service(user):
    existing = get_user_by_email(user.email)

    if existing:
        raise CustomException(400, "Email already registered")

    user_dict = user.dict()
    user_dict["password"] = hash_password(user.password)

    new_user = create_user_repo(user_dict)

    #  Generate token after registration
    token = create_access_token({"user_id": str(new_user["_id"])})

    return {
        "message": "User registered successfully",
        "user": user_helper(new_user),
        "access_token": token,
        "token_type": "bearer"
    }


def login_user_service(user):
    db_user = get_user_by_email(user.email)

    if not db_user:
        raise CustomException(404, "User not found")

    if not verify_password(user.password, db_user["password"]):
        raise CustomException(401, "Invalid credentials")

    token = create_access_token({"user_id": str(db_user["_id"])})

    return {"access_token": token, "token_type": "bearer"}