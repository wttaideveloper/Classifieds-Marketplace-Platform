from fastapi import Depends, HTTPException, status, Request
from jose import jwt, JWTError, ExpiredSignatureError
from app.db.database import SessionLocal
from app.core.config import settings

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing token")
    parts = auth_header.split()
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid token format")
    token = parts[1]
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
    except Exception as e:
        raise HTTPException(401, "Invalid token")
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    cust_id = payload.get("sub")
    if not cust_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return {
        "id": cust_id,
        "role": payload.get("role"),
        "email": payload.get("email")
    }

def require_roles(allowed_roles: list):
    def role_checker(current_user=Depends(get_current_user)):
        user_role = current_user.get("role")
        if user_role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Not authorized")
        return current_user
    return role_checker

def get_current_admin(current_user=Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user