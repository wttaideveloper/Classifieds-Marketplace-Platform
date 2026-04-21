from pydantic import BaseModel, EmailStr, Field

class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str