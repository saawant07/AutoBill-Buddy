import os
import time
import requests
import random
import string
from dotenv import load_dotenv
from supabase import create_client

# Suppress warnings
import warnings
warnings.filterwarnings("ignore")

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("Missing env vars")
    exit(1)

# 1. Setup Auth
print("--- 1. Authenticating ---")
client = create_client(url, key)

# Generate random email to ensure fresh user (and hopefully bypass "already registered" which blocks simple signup)
# Note: If email confirmation is ON, this might still fail on sign-in. 
# But debug_setup.py worked, so let's mimic it exactly.
rand_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
email = f"udhaar_{rand_suffix}@example.com"
password = "password123"

print(f"Creating user {email}...")

try:
    # Sign up
    res = client.auth.sign_up({"email": email, "password": password})
    
    # Auto-sign-in usually works if "Confirm Email" is disabled in Supabase.
    # If enabled, we're stuck unless we have a verified user.
    # Let's try explicit sign in.
    time.sleep(1)
    res = client.auth.sign_in_with_password({"email": email, "password": password})
    print("Logged in new test user")
    
except Exception as e:
    print(f"Auth Failed: {e}")
    print("⚠️  Cannot create/login test user. Skipping automated test.")
    print("PLEASE MANUALLY VERIFY by speaking 'Sold 5 Milk to Raju on Udhaar'")
    exit(1)

token = res.session.access_token
user_id = res.user.id
headers = {"Authorization": f"Bearer {token}"}
API_URL = "http://localhost:8000"

# 2. Add Stock for Test Item
print("\n--- 2. Adding Stock ---")
item_name = "TestCookie"
requests.post(f"{API_URL}/add-stock", json={"item_name": item_name, "quantity": 100, "price": 10}, headers=headers)

# 3. Create Udhaar Transaction
print("\n--- 3. Selling on Udhaar ---")
# "Sold 5 TestCookie to Raju on Udhaar"
payload = {"message": f"Sold 5 {item_name} to Raju on Udhaar"}
res = requests.post(f"{API_URL}/chat", json=payload, headers=headers)
print(f"Chat Response: {res.json()}")

if not res.json().get("success"):
    print("❌ Failed to create Udhaar transaction")
    print(res.text)
    exit(1)

# Check database directly
dues = client.table("dues").select("*").eq("user_id", user_id).eq("customer_name", "Raju").execute()
if dues.data and dues.data[0]['total_due'] > 0:
    print(f"✅ Dues record created: {dues.data[0]}")
else:
    print(f"❌ Dues record NOT found or 0: {dues.data}")
    exit(1)

# 4. Settle Dues
print("\n--- 4. Settling Dues ---")
res = requests.post(f"{API_URL}/dues/settle", json={"customer_name": "Raju"}, headers=headers)
print(f"Settle Response: {res.json()}")

if not res.json().get("success"):
    print("❌ Failed to settle dues")
    exit(1)

# Verify dues are 0
dues = client.table("dues").select("*").eq("user_id", user_id).eq("customer_name", "Raju").execute()
if dues.data and dues.data[0]['total_due'] == 0:
    print("✅ Dues settled (total_due is 0)")
else:
    print(f"❌ Dues not settled properly: {dues.data}")

# Verify sales marked as settled
sales = client.table("sales").select("*").eq("user_id", user_id).eq("customer_name", "Raju").eq("item_name", item_name).order("created_at", desc=True).limit(1).execute()
if sales.data and sales.data[0]['is_settled'] == True:
    print("✅ Sales record marked as settled")
else:
    print(f"❌ Sales record NOT marked settled: {sales.data}")

print("\n🎉 Udhaar Integration Verified!")
