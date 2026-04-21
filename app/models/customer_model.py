def customer_helper(user) -> dict:
    return {
        "id": str(user["_id"]),
        "firstName": user.get("firstName"),
        "lastName": user.get("lastName"),
        "email": user.get("email"),
        "mobileNumber": user.get("mobileNumber")
    }