import os
import random
import string
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("Missing env vars")
    exit(1)

client = create_client(url, key)

rand_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
email = f"test_{rand_suffix}@example.com"
password = "password123"

print(f"Creating user {email}...")

try:
    res = client.auth.sign_up({"email": email, "password": password})
    if res.user:
        print(f"Sign up result: User ID {res.user.id}")
    else:
        print("Sign up returned no user.")
    
    print("Attempting sign in...")
    try:
        res = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:
        print(f"Sign in failed: {e}")
        exit(1)
        
    print(f"Sign in success. Token len: {len(res.session.access_token)}")
    token = res.session.access_token
    
    # Test method 1: Client with session (stateful)
    print("Test 1: Stateful client query...")
    try:
        resp = client.table("inventory").select("*").execute()
        print(f"Inventory (Stateful): {resp.data}")
    except Exception as e:
        print(f"Stateful Query Error: {e}")

    # Test method 2: New client with manual auth (Stateless - like main.py)
    print("Test 2: Stateless client with postgrest.auth()...")
    try:
        new_client = create_client(url, key)
        new_client.postgrest.auth(token)
        resp = new_client.table("inventory").select("*").execute()
        print(f"Inventory (Stateless): {resp.data}")
    except Exception as e:
        print(f"Stateless Query Error: {e}")

except Exception as e:
    print(f"Fatal Error: {e}")
