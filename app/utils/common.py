from datetime import datetime

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