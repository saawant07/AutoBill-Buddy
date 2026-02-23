import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("Missing env vars")
    exit(1)

client = create_client(url, key)

print("Checking for 'cost_price' column...")

# Check Inventory
try:
    res = client.table("inventory").select("cost_price").limit(1).execute()
    print("✅ 'cost_price' found in 'inventory' table.")
except Exception as e:
    print(f"❌ 'cost_price' NOT found in 'inventory' table. Error: {e}")

# Check Sales
try:
    res = client.table("sales").select("cost_price").limit(1).execute()
    print("✅ 'cost_price' found in 'sales' table.")
except Exception as e:
    print(f"❌ 'cost_price' NOT found in 'sales' table. Error: {e}")
