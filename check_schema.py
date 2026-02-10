import os
import time
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("Missing env vars")
    exit(1)

# Force a new client + maybe a small delay?
client = create_client(url, key)

print("Checking schema (Attempt 2)...")

try:
    # Try an insert that would fail if column doesn't exist?
    # No, that's risky. Let's try RPC or just raw select again but printing full error
    
    print("\n--- Checking 'sales' table columns ---")
    try:
        # Select specific new columns
        res = client.table("sales").select("payment_mode, is_settled").limit(1).execute()
        print(f"✅ Columns found. Data sample: {res.data}")
    except Exception as e:
        print(f"❌ Columns missing in 'sales'. Error details: {e}")

    print("\n--- Checking 'dues' table ---")
    try:
        res = client.table("dues").select("count", count="exact").execute()
        print(f"✅ 'dues' table exists. Count: {res.count}")
    except Exception as e:
        print(f"❌ 'dues' table access failed. Error details: {e}")

except Exception as e:
    print(f"General Error: {e}")
