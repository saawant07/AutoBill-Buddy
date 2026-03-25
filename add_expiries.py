import os
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", SUPABASE_KEY)

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: Missing Supabase credentials in .env")
    exit(1)

# Use service key if available to bypass RLS, otherwise fallback to anon key
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def main():
    print("Fetching inventory items...")
    
    # Fetch all items
    response = supabase.table("inventory").select("id, item_name, expiry_date").execute()
    items = response.data
    
    if not items:
        print("No items found in inventory.")
        return

    print(f"Found {len(items)} items. Updating expiries...")
    
    updated_count = 0
    now = datetime.now()
    
    for item in items:
        # Generate a random expiry date between 30 and 365 days from now
        random_days = random.randint(30, 365)
        new_expiry_date = (now + timedelta(days=random_days)).strftime('%Y-%m-%d')
        
        # Update the item
        try:
            supabase.table("inventory").update({
                "expiry_date": new_expiry_date
            }).eq("id", item["id"]).execute()
            
            print(f"Updated '{item['item_name']}' with expiry date: {new_expiry_date}")
            updated_count += 1
        except Exception as e:
            print(f"Failed to update item {item['id']} ({item['item_name']}): {e}")

    print(f"\nSuccessfully updated {updated_count} items with new expiry dates!")

if __name__ == "__main__":
    main()
