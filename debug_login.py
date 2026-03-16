
from supabase import create_client, Client

# Hardcoded from .env
url = "https://mhnueaafadnlweezmemc.supabase.co"
key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1obnVlYWFmYWRubHdlZXptZW1jIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzA1Mzc3OTcsImV4cCI6MjA4NjExMzc5N30.IuweIlK8DP0VY3y9a7DMuftn-XnuovKa39_2pcuj6ow"

try:
    client = create_client(url, key)
except Exception as e:
    print(f"Client creation failed: {e}")
    exit(1)

email = "guest@autobill.com"
password = "guest1234"

print(f"Trying login for {email}...")
try:
    auth_response = client.auth.sign_in_with_password({"email": email, "password": password})
    user = auth_response.user
    session = auth_response.session
    print("Login Success!")
    print(f"User ID: {user.id}")
except Exception as e:
    print(f"Login Failed: {e}")
    # Try signup
    print("Trying signup...")
    try:
        auth_response = client.auth.sign_up({
            "email": email, "password": password,
            "options": {"data": {"full_name": "Public Demo Store"}}
        })
        user = auth_response.user
        session = auth_response.session
        if session:
            print("Signup Success & Logged In!")
            print(f"User ID: {user.id}")
        elif user:
            print(f"Signup Success but Email verification needed. User ID: {user.id}")
        else:
            print("Signup returned no user session/data??")
            print(auth_response)
            
    except Exception as e2:
        import traceback
        traceback.print_exc()
        print(f"Signup Failed: {e2}")
