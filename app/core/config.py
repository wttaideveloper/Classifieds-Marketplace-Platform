# from pydantic_settings import BaseSettings

# class Settings(BaseSettings):
#     #  Database
#     DATABASE_URL: str

#     #  JWT
#     SECRET_KEY: str
#     ALGORITHM: str
#     ACCESS_TOKEN_EXPIRE_MINUTES: int

#     # Google OAuth
#     GOOGLE_CLIENT_ID: str
#     GOOGLE_CLIENT_SECRET: str
#     GOOGLE_REDIRECT_URI: str

#     # send email
#     email_user: str
#     email_pass: str

#     class Config:
#         env_file = ".env"

# settings = Settings()


from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # Google OAuth
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str

    # send email
    email_user: str
    email_pass: str


    class Config:
        env_file = ".env"

settings = Settings()