import smtplib
from email.mime.text import MIMEText
from app.core.config import settings


def send_email(to_email: str, reset_link: str):
    try:
        msg = MIMEText(
            f"Click here to reset your password:\n\n{reset_link}",
            "plain"
        )

        msg["Subject"] = "Password Reset"
        msg["From"] = settings.email_user
        msg["To"] = to_email

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(settings.email_user, settings.email_pass)
            server.send_message(msg)

        return True

    except Exception as e:
        print(f"Email sending failed: {str(e)}")
        return False