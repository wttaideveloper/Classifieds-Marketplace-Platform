from datetime import datetime
import random

def success_response(data=None, message="Success", status_code=200):
    return {
        "status": "success",
        "message": message,
        "status_code": status_code,
        "data": data,
        "timestamp": datetime.utcnow().isoformat()
    }

def error_response(message="Error", status_code=400, data=None):
    return {
        "status": "error",
        "message": message,
        "status_code": status_code,
        "data": data,
        "timestamp": datetime.utcnow().isoformat()
    }

def generate_booking_number():
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    random_number = random.randint(1000, 9999)

    return f"BOOK-{timestamp}-{random_number}"