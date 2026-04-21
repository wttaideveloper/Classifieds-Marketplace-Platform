import smtplib
from email.mime.text import MIMEText
import os
from app.core.config import settings

def send_email(to_email, reset_link):
    msg = MIMEText(f"Click here to reset password: {reset_link}")
    msg["Subject"] = "Password Reset"
    msg["From"] = settings.email_user  
    msg["To"] = to_email

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()

    # Use App Password (NOT normal Gmail password)
    server.login(settings.email_user, settings.email_pass)
    server.send_message(msg)
    server.quit()