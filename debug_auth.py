import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

print("Initializing client...")
try:
    client = create_client(url, key)
    print("Client initialized.")
    
    print(f"Has postgrest attribute? {hasattr(client, 'postgrest')}")
    if hasattr(client, 'postgrest'):
        print(f"Postgrest client type: {type(client.postgrest)}")
        print(f"Has auth method? {hasattr(client.postgrest, 'auth')}")
        
        # Try calling it
        try:
            client.postgrest.auth("fake_token")
            print("client.postgrest.auth('fake_token') call successful.")
        except Exception as e:
            print(f"client.postgrest.auth('fake_token') failed: {e}")

except Exception as e:
    print(f"Error: {e}")
