import os
import json
import re
from datetime import datetime, timedelta
import asyncio
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
import google.generativeai as genai

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Global client for public operations only
supabase_anon: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# Guest mode bypass — uses anon client directly (RLS is disabled)
GUEST_MAGIC_TOKEN = "GUEST_MODE_NO_AUTH"
GUEST_USER_ID = "933fc862-30f9-45ef-b83f-c9d57f1ebfc6"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

# ============================================================================
# PER-REQUEST SUPABASE CLIENT (For RLS to work)
# ============================================================================
def get_user_client(authorization: Optional[str] = Header(None)) -> tuple[Client, str]:
    """
    Creates a fresh Supabase client for each request using the user's JWT token.
    This ensures Row Level Security (RLS) policies are applied correctly.
    For guest mode (GUEST_MAGIC_TOKEN), bypasses auth and uses anon client.
    Returns: (supabase_client, user_id)
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = authorization.replace("Bearer ", "")
    
    # Guest mode bypass — no Supabase Auth needed
    if token == GUEST_MAGIC_TOKEN:
        guest_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return guest_client, GUEST_USER_ID
    
    # Create a new client with the user's token
    user_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # Set the auth token so RLS policies will see this user
    try:
        user_client.auth.set_session(token, token)  # access_token, refresh_token
    except Exception as e:
        print(f"Set session error (ignoring): {e}")
        # Continue anyway - get_user will validate the token
    
    # Get user_id from the token
    try:
        user_response = user_client.auth.get_user(token)
        user_id = user_response.user.id
    except Exception as e:
        print(f"Auth Error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    return user_client, user_id

class ChatRequest(BaseModel):
    message: str


@app.get("/inventory")
async def get_inventory(auth: tuple = Depends(get_user_client)):
    try:
        db, user_id = auth
        # Fetch all batches
        response = db.table("inventory").select("*").eq("user_id", user_id).execute()
        raw_inventory = response.data
        
        # Aggregate Batches
        aggregated = {}
        for row in raw_inventory:
            name = row['item_name']
            if name not in aggregated:
                aggregated[name] = {
                    "item_name": name,
                    "stock_quantity": 0,
                    "price": row['price'],
                    "cost_price": row.get('cost_price', 0),
                    "expiry_date": None, # Start with None to avoid picking up 0-stock dates
                    "ids": []
                }
            
            agg = aggregated[name]
            agg["stock_quantity"] += row['stock_quantity']
            agg["ids"].append(row['id'])
            
            # Prefer a non-zero price if available across batches
            if row['price'] is not None and row['price'] > 0:
                if agg['price'] is None or agg['price'] == 0:
                    agg['price'] = row['price']
                    
            # Prefer a non-zero cost_price if available across batches
            row_cp = row.get('cost_price', 0)
            if row_cp is not None and row_cp > 0:
                if agg['cost_price'] is None or agg['cost_price'] == 0:
                    agg['cost_price'] = row_cp
            
            # Keep earliest non-null expiry from ACTIVE batches
            if row['stock_quantity'] > 0:
                current_exp = row['expiry_date']
                if current_exp:
                    if agg['expiry_date'] is None or current_exp < agg['expiry_date']:
                        agg['expiry_date'] = current_exp
                    
        # Convert to list and sort by lowest stock first
        result = list(aggregated.values())
        result.sort(key=lambda x: x['stock_quantity'])
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Inventory Error: {e}")
        return []

@app.get("/prices")
async def get_all_prices(auth: tuple = Depends(get_user_client)):
    try:
        db, user_id = auth
        ITEM_PRICES, _ = get_user_inventory_data(db, user_id)
        return ITEM_PRICES
    except Exception as e:
        print(f"Prices Error: {e}")
        return DEFAULT_PRICES

# Default prices global
DEFAULT_PRICES = {
    "Milk": 60, "Bread": 40, "Eggs": 7, "Butter": 55, "Cheese": 100, "Paneer": 80, "Curd": 45,
    "Rice": 50, "Sugar": 45, "Salt": 25, "Flour": 35, "Wheat": 35, "Atta": 40, "Maida": 40, "Suji": 50, "Poha": 45,
    "Dal": 120, "Toor Dal": 140, "Moong Dal": 130, "Chana Dal": 90, "Urad Dal": 120, "Rajma": 150, "Chana": 80,
    "Tea": 250, "Coffee": 400,
    "Oil": 150, "Ghee": 550, "Mustard Oil": 180, "Turmeric": 200, "Red Chilli": 300, "Cumin": 350, "Coriander": 150,
    "Biscuits": 30, "Chips": 20, "Noodles": 15, "Soap": 40, "Detergent": 120, "Toothpaste": 80,
}

# Helper to fetch inventory and build price dict
def get_user_inventory_data(db, user_id):
    # Fetch user's inventory
    user_inventory = {}
    try:
        inv_response = db.table("inventory").select("item_name, price, stock_quantity").eq("user_id", user_id).execute()
        user_inventory = {item['item_name']: item for item in inv_response.data} if inv_response.data else {}
    except Exception as e:
        print(f"[INVENTORY] Error fetching inventory: {e}")
        import traceback; traceback.print_exc()

    # Build ITEM_PRICES
    ITEM_PRICES = DEFAULT_PRICES.copy()
    for item_name, item_data in user_inventory.items():
        if item_name not in ITEM_PRICES:
            ITEM_PRICES[item_name] = item_data.get('price') or 0
        elif item_data.get('price'):
            ITEM_PRICES[item_name] = item_data['price']
    
    return ITEM_PRICES, user_inventory

# Helper for fuzzy matching
def fuzzy_match_item(word, available_items):
    # Dictionaries for fuzzy matching (moved outside to save space/recreation, but defined here for closure)
    VOICE_TYPOS = {
        'keji': 'kg', 'kaji': 'kg', 'kaji': 'kg', 'kilo': 'kg', 'kilos': 'kg', 'kilogram': 'kg',
        'rise': 'rice', 'rais': 'rice', 'raice': 'rice',
        'tee': 'tea', 'chai': 'tea',
        'melk': 'milk', 'melku': 'milk',
        'suger': 'sugar', 'sugur': 'sugar',
        'flor': 'flour', 'flower': 'flour',
        'bred': 'bread', 'brad': 'bread',
        'ags': 'eggs', 'ags': 'eggs', 'aggs': 'eggs',
        'ghea': 'ghee', 'ghi': 'ghee',
        'panir': 'paneer', 'paner': 'paneer',
        'coffe': 'coffee', 'koffee': 'coffee', 'cofee': 'coffee',
        'biskit': 'biscuits', 'biscuit': 'biscuits',
        'chiips': 'chips', 'chip': 'chips',
        'noodle': 'noodles',
        'maggie': 'maggi', 'maagi': 'maggi',
    }
    
    word_lower = word.lower().strip()
    if word_lower in VOICE_TYPOS:
        word_lower = VOICE_TYPOS[word_lower].lower()
    
    # Exact match
    for item in available_items:
        if item.lower() == word_lower:
            return item
    
    # Partial match
    for item in available_items:
        if item.lower() in word_lower or word_lower in item.lower():
            return item
            
    # Prefix match
    if len(word_lower) >= 3:
        for item in available_items:
            if item.lower().startswith(word_lower[:3]) or word_lower.startswith(item.lower()[:3]):
                return item
    return None

def parse_message_locally(message, available_items, custom_aliases=None):
    text = message.lower()
    
    if custom_aliases is None:
        custom_aliases = {}
    
    # Typos and Numbers dictionaries
    WORD_TO_NUM = {
        'zero': 0, 'one': 1, 'won': 1, 'two': 2, 'too': 2, 'to': 2, 'tu': 2,
        'three': 3, 'tree': 3, 'free': 3, 'four': 4, 'for': 4, 'ford': 4, 'fore': 4,
        'five': 5, 'fife': 5, 'six': 6, 'sex': 6, 'sax': 6, 'seven': 7, 'saven': 7,
        'eight': 8, 'ate': 8, 'ait': 8, 'nine': 9, 'nain': 9, 'ten': 10, 'tan': 10,
        'eleven': 11, 'twelve': 12, 'half': 0.5, 'quarter': 0.25,
        'ek': 1, 'do': 2, 'teen': 3, 'char': 4, 'paanch': 5, 'panch': 5,
        'chhe': 6, 'chay': 6, 'saat': 7, 'aath': 8, 'nau': 9, 'das': 10,
        'gyarah': 11, 'barah': 12, 'terah': 13, 'chaudah': 14, 'pandrah': 15,
        'dhai': 2.5, 'adha': 0.5, 'dedh': 1.5, 'paune': 0.75,
        'aadha': 0.5, 'adhaa': 0.5, 'pav': 0.25, 'paav': 0.25, 'sawa': 1.25,
        'double': 2, 'triple': 3, 'single': 1,
    }

    # Expanded typos including Hindi
    EXPANDED_VOICE_TYPOS = {
        'keji': 'kg', 'kaji': 'kg', 'kaji': 'kg', 'kilo': 'kg', 'kilos': 'kg', 'kilogram': 'kg',
        'doodh': 'milk', 'dudh': 'milk', 'dudth': 'milk', 'melk': 'milk', 'malk': 'milk', 'milkk': 'milk',
        'chawal': 'rice', 'chaawal': 'rice', 'chaval': 'rice', 'rise': 'rice', 'rais': 'rice', 'raice': 'rice', 'ricee': 'rice',
        'cheeni': 'sugar', 'chini': 'sugar', 'shakkar': 'sugar', 'suger': 'sugar', 'sugur': 'sugar', 'sugarr': 'sugar',
        'aloo': 'potato', 'alu': 'potato', 'aaloo': 'potato',
        'pyaz': 'onion', 'pyaaz': 'onion', 'kanda': 'onion',
        'tamatar': 'tomato', 'tamater': 'tomato',
        'anda': 'eggs', 'ande': 'eggs', 'anday': 'eggs', 'ags': 'eggs', 'aggs': 'eggs', 'eggz': 'eggs', 'eg': 'eggs',
        'namak': 'salt', 'namkeen': 'salt',
        'tel': 'oil', 'teil': 'oil',
        'makhan': 'butter', 'makkhan': 'butter', 'buttar': 'butter', 'butr': 'butter',
        'dahi': 'curd', 'dahee': 'curd',
        'roti': 'bread', 'rotee': 'bread', 'bred': 'bread', 'brad': 'bread', 'breads': 'bread',
        'daal': 'dal', 'dhaal': 'dal', 'dhal': 'dal',
        'chees': 'cheese', 'cheez': 'cheese', 'cheeze': 'cheese',
        'panneer': 'paneer', 'pneer': 'paneer', 'panir': 'paneer', 'paner': 'paneer',
        'ataa': 'atta', 'aata': 'atta', 'aatta': 'atta',
        'maida': 'maida', 'mayda': 'maida',
        'biskut': 'biscuits', 'biscut': 'biscuits', 'biskoot': 'biscuits', 'biskit': 'biscuits',
        'sabun': 'soap', 'saabun': 'soap',
        'maggie': 'maggi', 'maagi': 'maggi', 'noodle': 'noodles',
        'tooothpaste': 'toothpaste', 'toothpast': 'toothpaste', 'colgate': 'toothpaste',
        'tee': 'tea', 'chai': 'tea', 'patti': 'tea',
        'coffe': 'coffee', 'koffee': 'coffee', 'cofee': 'coffee',
        'jeera': 'cumin', 'jira': 'cumin', 'zeera': 'cumin',
    }
    
    # Merge custom aliases
    EXPANDED_VOICE_TYPOS.update(custom_aliases)


    # Detect Payment Mode & Customer
    detected_mode = 'Cash'
    detected_customer = 'Walk-in'
    
    udhaar_keywords = ['udhaar', 'udhar', 'udhhaar', 'udhar', 'credit', 'khata', 'khate', 'khatte', 'udhaari', 'udhari', 'uthaar', 'uthar', 'udhaar pe', 'udhar pe', 'credit pe', 'on credit']
    for kw in udhaar_keywords:
        if kw in text:
            detected_mode = 'Udhaar'
            break
            
    customer_patterns = [
        r'(?:to|for)\s+([A-Za-z]+?)\s+(?:on|udhaar|udhar|credit|khata|khatte)',
        r'([A-Za-z]+?)\s+(?:ko|ka|ki|ke|se)(?:\s|$)',
        r'(?:to|for)\s+([A-Za-z]+?)(?:\s|$)',
        r'([A-Za-z]+?)\s+(?:udhaar|udhar|credit|khata|khatte)',
    ]
    
    original_text = text
    for pat in customer_patterns:
        m = re.search(pat, original_text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().title()
            non_names = {'on', 'the', 'and', 'sold', 'sell', 'sale', 'give', 'some', 'also', 'more', 'cash', 'udhaar', 'milk', 'bread', 'sugar', 'rice', 'oil', 'eggs', 'butter', 'cheese', 'paneer', 'curd', 'atta', 'dal', 'tea', 'coffee', 'ghee', 'soap', 'chips', 'noodles', 'biscuits', 'toothpaste', 'detergent', 'flour', 'salt', 'wheat', 'maida', 'suji', 'poha', 'jeera', 'cumin', 'khatte', 'khata'}
            if candidate.lower() not in non_names and len(candidate) >= 2 and not candidate.replace('.','').isdigit():
                detected_customer = candidate
                break

    # Clean text
    text = re.sub(r'\b(udhaar|udhar|credit|khata|khate|khatte|udhaari)\b', ' ', text, flags=re.IGNORECASE)
    if detected_customer != 'Walk-in':
        text = re.sub(rf'\b{re.escape(detected_customer)}\b', ' ', text, flags=re.IGNORECASE)
    
    # NEW: Remove common Hindi stop words and general filler words
    text = re.sub(r'\b(sold|sell|sale|selling|please|and|aur|the|a|an|some|of|also|give|add|more|i|want|need|get|me|us|becho|bech|do|de|dena|le|lo|lena|karo|nu|no|ko|ka|ki|ke|se|pe|p|par|on|for|to)\b', ' ', text, flags=re.IGNORECASE)
    
    # Separate stuck digits and text (e.g., "2tel" -> "2 tel")
    text = re.sub(r'(\d+)([a-zA-Z]+)', r'\1 \2', text)

    for typo, correction in EXPANDED_VOICE_TYPOS.items():
        text = re.sub(rf'\b{typo}\b', correction, text, flags=re.IGNORECASE)
        
    for word, num in WORD_TO_NUM.items():
        text = re.sub(rf'\b{word}\b', str(num), text, flags=re.IGNORECASE)
        
    # Remove units
    text = re.sub(r'\b(kg|kgs|kilo|kilos|liter|liters|ltr|ml|gram|grams|gm|gms|g|piece|pieces|pcs|packet|packets|pack)\b', ' ', text, flags=re.IGNORECASE)
    text = ' '.join(text.split())

    items_found = []
    words = text.split()
    consumed_indices = set()  # track which word positions are already matched
    
    # Pattern 1: "2 amul milk" or "2 milk" (number followed by item words)
    # Try matching number + next 2 words first, then number + next 1 word
    i = 0
    while i < len(words):
        # Check if current word is a number
        num_match = re.match(r'^(\d+(?:\.\d+)?)$', words[i])
        if num_match and i not in consumed_indices:
            qty = float(num_match.group(1))
            matched = False
            
            # Try 2-word item: "2 amul milk" (skip if either word is a digit)
            if (i + 2 < len(words) and (i+1) not in consumed_indices and (i+2) not in consumed_indices
                    and not words[i+1].replace('.','').isdigit() and not words[i+2].replace('.','').isdigit()):
                combo2 = f"{words[i+1]} {words[i+2]}"
                match2 = fuzzy_match_item(combo2, available_items)
                if match2 and not any(it["item"] == match2 for it in items_found):
                    items_found.append({"item": match2, "qty": qty})
                    consumed_indices.update({i, i+1, i+2})
                    i += 3
                    matched = True
            
            # Try 1-word item: "2 milk"
            if not matched and i + 1 < len(words) and (i+1) not in consumed_indices:
                match1 = fuzzy_match_item(words[i+1], available_items)
                if match1 and not any(it["item"] == match1 for it in items_found):
                    items_found.append({"item": match1, "qty": qty})
                    consumed_indices.update({i, i+1})
                    i += 2
                    matched = True
            
            if not matched:
                i += 1
        else:
            i += 1
    
    # Pattern 2: "amul milk 2" or "milk 2" (item words followed by number)
    i = 0
    while i < len(words):
        if i in consumed_indices:
            i += 1
            continue
        
        # Try 2-word item + number: "amul milk 2"
        if i + 2 < len(words) and (i+1) not in consumed_indices and (i+2) not in consumed_indices:
            num_match = re.match(r'^(\d+(?:\.\d+)?)$', words[i+2])
            if num_match:
                combo = f"{words[i]} {words[i+1]}"
                match2 = fuzzy_match_item(combo, available_items)
                if match2 and not any(it["item"] == match2 for it in items_found):
                    items_found.append({"item": match2, "qty": float(num_match.group(1))})
                    consumed_indices.update({i, i+1, i+2})
                    i += 3
                    continue
        
        # Try 1-word item + number: "milk 2"
        if i + 1 < len(words) and (i+1) not in consumed_indices:
            num_match = re.match(r'^(\d+(?:\.\d+)?)$', words[i+1])
            if num_match and not words[i].replace('.', '').isdigit():
                match1 = fuzzy_match_item(words[i], available_items)
                if match1 and not any(it["item"] == match1 for it in items_found):
                    items_found.append({"item": match1, "qty": float(num_match.group(1))})
                    consumed_indices.update({i, i+1})
                    i += 2
                    continue
        
        i += 1

    # Pattern 3: Standalone items — try multi-word matches first to avoid duplicates
    # e.g. "britannia bread" should match "Britannia Bread" only, not also "Bread"
    
    # 3a: Try 3-word combinations
    for i in range(len(words) - 2):
        if i in consumed_indices or (i+1) in consumed_indices or (i+2) in consumed_indices:
            continue
        combo = f"{words[i]} {words[i+1]} {words[i+2]}"
        if combo.replace('.', '').replace(' ', '').isdigit():
            continue
        matched_item = fuzzy_match_item(combo, available_items)
        if matched_item and not any(it["item"] == matched_item for it in items_found):
            items_found.append({"item": matched_item, "qty": 1.0})
            consumed_indices.update({i, i+1, i+2})
    
    # 3b: Try 2-word combinations (e.g. "britannia bread", "amul milk", "toor dal")
    for i in range(len(words) - 1):
        if i in consumed_indices or (i+1) in consumed_indices:
            continue
        combo = f"{words[i]} {words[i+1]}"
        if combo.replace('.', '').replace(' ', '').isdigit():
            continue
        matched_item = fuzzy_match_item(combo, available_items)
        if matched_item and not any(it["item"] == matched_item for it in items_found):
            items_found.append({"item": matched_item, "qty": 1.0})
            consumed_indices.update({i, i+1})
    
    # 3c: Single-word fallback for unconsumed words
    for i, word in enumerate(words):
        if i in consumed_indices:
            continue
        if not word.replace('.', '').isdigit():
            matched_item = fuzzy_match_item(word, available_items)
            if matched_item and not any(it["item"] == matched_item for it in items_found):
                items_found.append({"item": matched_item, "qty": 1.0})
                
    return items_found, detected_mode, detected_customer

@app.post("/parse-order")
async def parse_order_endpoint(req: ChatRequest, auth: tuple = Depends(get_user_client)):
    try:
        db, user_id = auth
        print(f"[PARSE] User {user_id} said: '{req.message}'")
        
        ITEM_PRICES, user_inventory = get_user_inventory_data(db, user_id)
        available_items = list(ITEM_PRICES.keys())
        
        # Fetch custom aliases from DB (Global for all users)
        custom_aliases = {}
        try:
            alias_res = db.table("item_aliases").select("item_name, alias").execute()
            if alias_res.data:
                for row in alias_res.data:
                    custom_aliases[row['alias'].lower()] = row['item_name']
        except Exception as e:
            print(f"[PARSE] Error fetching aliases: {e}")
        
        # 1. Try Local Parsing First
        items_to_sell, payment_mode, customer_name = parse_message_locally(req.message, available_items, custom_aliases)
        
        # 2. AI Fallback if Local finds nothing
        if not items_to_sell:
            try:
                prompt = f"""
                You are a smart cashier. Parse: "{req.message}"
                Items available: {json.dumps(available_items)}
                Return ONLY JSON: {{"items": [{{"item": "Name", "qty": 1}}], "payment_mode": "Cash"/"Udhaar", "customer_name": "Name"}}
                """
                response = model.generate_content(prompt)
                text = response.text.strip()
                if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
                
                ai_parsed = json.loads(text)
                ai_items = ai_parsed if isinstance(ai_parsed, list) else ai_parsed.get("items", [])
                
                # Update metadata from AI if local didn't find anything specific
                if isinstance(ai_parsed, dict):
                    if payment_mode == 'Cash' and ai_parsed.get("payment_mode") == "Udhaar":
                        payment_mode = "Udhaar"
                    if customer_name == 'Walk-in' and ai_parsed.get("customer_name") != "Walk-in":
                        customer_name = ai_parsed.get("customer_name", "Walk-in").strip().title()
                
                for item_data in ai_items:
                    item_name = item_data.get("item", "").strip().title()
                    qty = float(item_data.get("qty", 1))
                    matched = fuzzy_match_item(item_name, available_items)
                    if matched and qty > 0:
                        items_to_sell.append({"item": matched, "qty": qty})
            except Exception as ai_err:
                print(f"AI Parse Fallback Error (Quota/Network): {ai_err}")
                # AI failed, but we still have local results (which are empty here)
        
        # 3. Prepare response with pricing
        parsed_items = []
        for s in items_to_sell:
            name = s["item"]
            price = ITEM_PRICES.get(name, 0)
            parsed_items.append({
                "item_name": name,
                "quantity": s["qty"],
                "unit_price": price,
                "total_price": s["qty"] * price
            })
            
        return {
            "success": len(parsed_items) > 0,
            "items": parsed_items,
            "payment_mode": payment_mode,
            "customer_name": customer_name
        }
    except Exception as e:
        print(f"Parse Endpoint Fatal Error: {e}")
        return {"success": False, "message": "Could not understand order", "error_type": "fatal"}

class ConfirmOrderRequest(BaseModel):
    items: list[dict] # [{"item_name": "Milk", "quantity": 2, "total_price": 120}]
    payment_mode: str
    customer_name: str

@app.post("/confirm-order")
async def confirm_order_endpoint(req: ConfirmOrderRequest, auth: tuple = Depends(get_user_client)):
    db, user_id = auth
    import uuid
    transaction_id = str(uuid.uuid4())[:8]
    
    ITEM_PRICES, user_inventory = get_user_inventory_data(db, user_id)
    
    total_order_price = 0
    results = []
    failed = []
    
    for item_data in req.items:
        item = item_data["item_name"].strip().title()
        qty = float(item_data["quantity"])
        
        # Determine price (trust frontend price since user explicitly confirms it)
        # Fallback to backend price if missing
        fallback_unit_price = ITEM_PRICES.get(item, 0)
        unit_price = item_data.get("unit_price", fallback_unit_price)
        price = item_data.get("total_price", qty * unit_price)
        
        # If backend price was 0, learn the new price from this transaction
        if fallback_unit_price == 0 and unit_price > 0:
            try:
                db.table("inventory").update({"price": unit_price}).eq("user_id", user_id).eq("item_name", item).execute()
            except:
                pass
        
        # Check Stock (Aggregate)
        batches = db.table("inventory").select("id, stock_quantity, cost_price").eq("user_id", user_id).eq("item_name", item).order("expiry_date", desc=False).execute().data
        
        current_stock = sum(b['stock_quantity'] for b in batches)
        
        if current_stock < qty:
            failed.append(f"{item} (Stock: {current_stock})")
            continue
            
        # Update Stock (FIFO) & Calculate Total Cost
        remaining_qty = qty
        total_cost_of_sold = 0
        
        for b in batches:
            if remaining_qty <= 0:
                break
                
            available = b['stock_quantity']
            batch_cp = b.get('cost_price', 0) or 0
            
            if available > remaining_qty:
                # Deduct from this batch
                new_batch_stock = available - remaining_qty
                db.table("inventory").update({"stock_quantity": new_batch_stock}).eq("id", b['id']).execute()
                total_cost_of_sold += remaining_qty * batch_cp
                remaining_qty = 0
            else:
                # Deplete this batch
                db.table("inventory").update({"stock_quantity": 0}).eq("id", b['id']).execute()
                total_cost_of_sold += available * batch_cp
                remaining_qty -= available
            
        # Record Sale
        db.table("sales").insert({
            "item_name": item,
            "quantity": qty,
            "total_price": price,
            "total_cost": total_cost_of_sold,
            "customer_name": req.customer_name,
            "user_id": user_id,
            "transaction_id": transaction_id,
            "payment_mode": req.payment_mode,
            "is_settled": req.payment_mode == 'Cash'
        }).execute()
        
        total_order_price += price
        results.append(f"{qty} {item}")
        
    # Update Dues if Udhaar
    if results and req.payment_mode == 'Udhaar' and req.customer_name != 'Walk-in':
        try:
            dues_check = db.table("dues").select("total_due").eq("user_id", user_id).eq("customer_name", req.customer_name).execute()
            if dues_check.data:
                new_due = dues_check.data[0]['total_due'] + total_order_price
                db.table("dues").update({"total_due": new_due, "last_updated": "now()"}).eq("user_id", user_id).eq("customer_name", req.customer_name).execute()
            else:
                db.table("dues").insert({"customer_name": req.customer_name, "total_due": total_order_price, "user_id": user_id}).execute()
        except Exception as e:
            print(f"Dues Update Error: {e}")
            
    if failed:
        return {"success": len(results) > 0, "message": f"Saved {len(results)} items. Failed: {', '.join(failed)}", "failed_items": failed}
    
    return {"success": True, "message": f"✅ Order Confirmed: ₹{total_order_price}", "transaction_id": transaction_id}


@app.get("/dues")
async def get_dues(auth: tuple = Depends(get_user_client)):
    try:
        db, user_id = auth
        response = db.table("dues").select("customer_name, total_due, last_updated").eq("user_id", user_id).gt("total_due", 0).order("total_due", desc=True).execute()
        return {"dues": response.data or []}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Dues Error: {e}")
        return {"dues": []}

@app.get("/dues/{customer_name}")
async def get_customer_dues(customer_name: str, auth: tuple = Depends(get_user_client)):
    from datetime import datetime, timezone, timedelta
    try:
        db, user_id = auth
        response = db.table("sales").select("item_name, quantity, total_price, created_at").eq("user_id", user_id).eq("customer_name", customer_name).eq("payment_mode", "Udhaar").order("created_at", desc=True).execute()
        
        transactions = []
        for s in (response.data or []):
            date_str = ""
            created = s.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    ist = dt + timedelta(hours=5, minutes=30)
                    date_str = ist.strftime("%d %b, %I:%M %p")
                except:
                    date_str = created
            transactions.append({
                "date_time": date_str,
                "item_name": s.get("item_name", ""),
                "quantity": s.get("quantity", 0),
                "total_price": s.get("total_price", 0)
            })
        
        # Get total due
        dues_check = db.table("dues").select("total_due").eq("user_id", user_id).eq("customer_name", customer_name).execute()
        total_due = dues_check.data[0]["total_due"] if dues_check.data else 0
        
        return {"customer_name": customer_name, "total_due": total_due, "transactions": transactions}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Customer Dues Error: {e}")
        return {"customer_name": customer_name, "total_due": 0, "transactions": []}

@app.get("/sales/today")
async def get_todays_sales(auth: tuple = Depends(get_user_client)):
    from datetime import datetime, timezone, timedelta
    try:
        db, user_id = auth
        # Use IST for "today" to match monthly calculations
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        today_ist = ist_now.strftime('%Y-%m-%d')
        
        # Convert IST start of day to UTC for database query
        today_start_ist = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_ist - timedelta(hours=5, minutes=30)
        
        response = db.table("sales").select("*").eq("user_id", user_id).gte("created_at", today_start_utc.isoformat()).execute()
        sales = response.data
        
        total_revenue = sum(s.get('total_price', 0) for s in sales)
        total_cost = sum(s.get('total_cost', 0) or 0 for s in sales)
        total_profit = total_revenue - total_cost
        
        total_quantity = sum(s.get('quantity', 0) for s in sales)
        total_sales = len(sales)
        
        item_summary = {}
        for s in sales:
            item = s.get('item_name', 'Unknown')
            item_summary[item] = item_summary.get(item, 0) + s.get('quantity', 0)
        
        from collections import OrderedDict
        orders = OrderedDict()
        legacy_counter = 0
        
        for s in reversed(sales):
            created = s.get("created_at", "")
            # Use transaction_id for grouping if available, else fallback to timestamp
            oid = s.get("transaction_id") or (created[:19] if created else None)
            if not oid:
                legacy_counter += 1
                oid = f"LEGACY_{legacy_counter}"
            
            if oid not in orders:
                time_str = ""
                if created:
                    try:
                        from datetime import timedelta
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        ist = dt + timedelta(hours=5, minutes=30)
                        time_str = ist.strftime("%I:%M %p")
                    except:
                        time_str = ""
                
                orders[oid] = {
                    "items": [],
                    "detailed_items": [],
                    "total_price": 0,
                    "order_id": oid,
                    "time": time_str,
                    "payment_mode": s.get("payment_mode", "Cash"),
                    "customer_name": s.get("customer_name", "Walk-in")
                }
            
            qty = s.get('quantity', 0)
            name = s.get('item_name', '?')
            total = s.get('total_price', 0)
            unit_price = total / qty if qty > 0 else 0
            
            orders[oid]["items"].append(f"{qty} {name}")
            orders[oid]["detailed_items"].append({
                "qty": qty,
                "name": name,
                "unit_price": round(unit_price, 2),
                "total": round(total, 2)
            })
            orders[oid]["total_price"] += total
        
        transactions = []
        order_num = len(orders)
        for oid, data in orders.items():
            transactions.append({
                "order": f"{order_num:04d}",
                "item": ", ".join(data["items"]),
                "detailed_items": data["detailed_items"],
                "qty": len(data["items"]),
                "price": round(data["total_price"], 2),
                "time": data["time"],
                "payment_mode": data.get("payment_mode", "Cash"),
                "customer_name": data.get("customer_name", "Walk-in")
            })
            order_num -= 1
        
        from datetime import timedelta
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        today_date = ist_now.strftime("%d %b %Y")
        
        return {
            "date": today_date,
            "total_revenue": round(total_revenue, 2),
            "total_profit": round(total_profit, 2),
            "total_sales": len(orders),
            "total_quantity": round(total_quantity, 2),
            "items_sold": [{"name": k, "qty": round(v, 2)} for k, v in item_summary.items()],
            "transactions": transactions[:10]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Sales Error: {e}")
        return {"total_revenue": 0, "total_profit": 0, "total_sales": 0, "total_quantity": 0, "items_sold": [], "transactions": []}

from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks

# ... (imports)

# Helper to generate aliases using Gemini
async def generate_aliases_task(item_name: str, user_id: str, db: Client):
    try:
        print(f"Generating aliases for: {item_name}")
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"""
        Generate 5-10 common voice-to-text typos, phonetic misspellings, and Hindi/Hinglish synonyms for the grocery item '{item_name}'.
        Return ONLY a JSON array of lowercase strings.
        Example for 'Milk': ["doodh", "dudh", "melk", "malk", "milkk"]
        Example for 'Sugar': ["cheeni", "chini", "shakkar", "suger"]
        """
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Clean up JSON if needed (remove markdown code blocks)
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text.rsplit("\n", 1)[0]
        
        import json
        aliases = json.loads(text)
        
        if aliases and isinstance(aliases, list):
            data = [{"item_name": item_name, "alias": a.lower()} for a in aliases]
            # Add exact name too if not present, to be safe
            if item_name.lower() not in aliases:
                data.append({"item_name": item_name, "alias": item_name.lower()})
                
            # Insert into item_aliases
            db.table("item_aliases").insert(data).execute()
            print(f"✅ Generated {len(data)} aliases for {item_name}")
            
    except Exception as e:
        print(f"❌ Alias Generation Error: {e}")

class AddStockRequest(BaseModel):
    item_name: str
    quantity: float
    price: Optional[float] = None
    cost_price: Optional[float] = None
    expiry_date: Optional[str] = None  # Format: YYYY-MM-DD

@app.post("/add-stock")
async def add_stock(req: AddStockRequest, background_tasks: BackgroundTasks, auth: tuple = Depends(get_user_client)):
    try:
        db, user_id = auth
        print(f"[add-stock] User {user_id} is adding item: {req.item_name}, qty: {req.quantity}, price: {req.price}")
        item = req.item_name.strip().title()
        qty = req.quantity
        expiry = req.expiry_date if req.expiry_date else None
        
        # Check if item is NEW (no inventory records for this item yet)
        # We do this check BEFORE inserting the new stock, to see if it was previously unknown.
        # Although, if we just insert, we can check if it existing count was 0.
        # Better: Check existing inventory count for this name.
        existing_check = db.table("inventory").select("id", count="exact").eq("user_id", user_id).eq("item_name", item).execute()
        is_new_item = existing_check.count == 0
        
        # 1. Update PRICE for ALL batches (logic unchanged) ...
        if req.price is not None:
             try:
                 db.table("inventory").update({"price": req.price}).eq("user_id", user_id).eq("item_name", item).execute()
             except:
                 pass
        
        # 2. Upsert specific batch
        query = db.table("inventory").select("id, stock_quantity").eq("user_id", user_id).eq("item_name", item)
        if expiry:
            query = query.eq("expiry_date", expiry)
        else:
            query = query.is_("expiry_date", "null")
            
        inv_check = query.execute()
        
        if inv_check.data:
            # Update existing batch
            current = inv_check.data[0]['stock_quantity']
            new_stock = current + qty
            batch_id = inv_check.data[0]['id']
            update_data = {"stock_quantity": new_stock}
            if req.price is not None:
                update_data["price"] = req.price
            if req.cost_price is not None:
                update_data["cost_price"] = req.cost_price
                
            db.table("inventory").update(update_data).eq("id", batch_id).execute()
            return {"message": f"✅ Added {qty} to {item} (Exp: {expiry or 'None'}). New stock: {new_stock}", "success": True}
        else:
            # Insert new batch
            insert_data = {
                "item_name": item, 
                "stock_quantity": qty, 
                "user_id": user_id,
                "expiry_date": expiry
            }
            if req.price is not None:
                insert_data["price"] = req.price
            if req.cost_price is not None:
                insert_data["cost_price"] = req.cost_price
            
            db.table("inventory").insert(insert_data).execute()
            
            # TRIGGER ALIAS GENERATION IF NEW
            if is_new_item:
                background_tasks.add_task(generate_aliases_task, item, user_id, db)
                
            return {"message": f"✅ Created {item} (Exp: {expiry or 'None'}) with stock: {qty}", "success": True}
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Add Stock Error: {e}")
        return {"message": f"❌ Error: {str(e)}", "success": False}

class ReduceStockRequest(BaseModel):
    item_name: str
    quantity: float

@app.post("/reduce-stock")
async def reduce_stock(req: AddStockRequest, auth: tuple = Depends(get_user_client)):
    # Simple reduce (for manual correction) - just reduces from ANY batch (latest/earliest?)
    # For manual correction, it's ambiguous which batch to reduce if we don't specify.
    # For now, let's implement FIFO reduction similar to sales.
    try:
        db, user_id = auth
        item = req.item_name.strip().title()
        qty = req.quantity
        
        # Fetch all batches ordered by expiry (Nulls last? Or nulls first? Usually null expiry means non-perishable, so last)
        # Postgres sorts nulls last by default in ASC.
        batches = db.table("inventory").select("id, stock_quantity, expiry_date").eq("user_id", user_id).eq("item_name", item).order("expiry_date", desc=False).execute().data
        
        if not batches:
             return {"message": f"❌ Item '{item}' not found", "success": False}
             
        total_stock = sum(b['stock_quantity'] for b in batches)
        if qty > total_stock:
             return {"message": f"❌ Cannot reduce by {qty}. Only {total_stock} in stock.", "success": False}
             
        remaining_to_reduce = qty
        
        for b in batches:
            if remaining_to_reduce <= 0:
                break
                
            available = b['stock_quantity']
            if available > remaining_to_reduce:
                # Reduce this batch and done
                new_qty = available - remaining_to_reduce
                db.table("inventory").update({"stock_quantity": new_qty}).eq("id", b['id']).execute()
                remaining_to_reduce = 0
            else:
                # Deplete this batch
                # Option: Delete the row if 0? Or keep it at 0? 
                # Let's keep at 0 for history/price reference, or user can delete manually.
                # Actually, better to keep at 0 so we don't lose the item definition if it's the last batch.
                db.table("inventory").update({"stock_quantity": 0}).eq("id", b['id']).execute()
                remaining_to_reduce -= available
                
        return {"message": f"✅ Reduced {item} by {qty}.", "success": True}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"message": f"❌ Error: {str(e)}", "success": False}

class DeleteItemRequest(BaseModel):
    item_name: str

@app.delete("/delete-item")
async def delete_item(req: DeleteItemRequest, auth: tuple = Depends(get_user_client)):
    try:
        db, user_id = auth
        item = req.item_name.strip().title()
        
        result = db.table("inventory").delete().eq("user_id", user_id).eq("item_name", item).execute()
        
        if result.data:
            return {"message": f"✅ Deleted '{item}' from inventory", "success": True}
        else:
            return {"message": f"❌ Item '{item}' not found", "success": False}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"message": f"❌ Error: {str(e)}", "success": False}

class SettleDuesRequest(BaseModel):
    customer_name: str
    amount: Optional[float] = None

@app.post("/dues/settle")
async def settle_dues(req: SettleDuesRequest, auth: tuple = Depends(get_user_client)):
    try:
        db, user_id = auth
        customer = req.customer_name.strip()
        amount_to_settle = req.amount
        
        dues_check = db.table("dues").select("id, total_due").eq("user_id", user_id).eq("customer_name", customer).execute()
        if not dues_check.data or dues_check.data[0]["total_due"] <= 0:
            return {"message": f"No outstanding dues for {customer}", "success": False}
        
        total_due = dues_check.data[0]["total_due"]
        
        # Handle Full vs Partial Settlement
        if amount_to_settle is None or amount_to_settle >= total_due:
            # Full Settle
            db.table("dues").update({"total_due": 0, "last_updated": "now()"}).eq("user_id", user_id).eq("customer_name", customer).execute()
            db.table("sales").update({"is_settled": True}).eq("user_id", user_id).eq("customer_name", customer).eq("payment_mode", "Udhaar").eq("is_settled", False).execute()
            msg = f"✅ Fully Settled ₹{round(total_due)} for {customer}"
            settled_val = total_due
        else:
            # Partial Settle
            new_due = total_due - amount_to_settle
            db.table("dues").update({"total_due": new_due, "last_updated": "now()"}).eq("user_id", user_id).eq("customer_name", customer).execute()
            
            # Record a negative sale to show payment in history
            db.table("sales").insert({
                "item_name": "Payment Received",
                "quantity": 1,
                "total_price": -amount_to_settle,
                "customer_name": customer,
                "user_id": user_id,
                "payment_mode": "Udhaar",
                "is_settled": True # Payments are effectively "settled" ledger entries
            }).execute()
            msg = f"✅ Received ₹{round(amount_to_settle)} from {customer}. Remaining: ₹{round(new_due)}"
            settled_val = amount_to_settle
            
        return {"message": msg, "success": True, "settled_amount": round(settled_val)}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"message": f"❌ Error: {str(e)}", "success": False}

@app.get("/analytics/weekly")
async def get_weekly_analytics(auth: tuple = Depends(get_user_client)):
    from datetime import datetime, timezone, timedelta
    try:
        db, user_id = auth
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        
        # Get last 7 days including today (oldest first)
        dates = [(ist_now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        
        weekly_data = {
            "dates": [d[5:] for d in dates], # Just MM-DD for label
            "cash": [0] * 7,
            "udhaar": [0] * 7
        }
        
        start_date = ist_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
        start_date_utc = start_date - timedelta(hours=5, minutes=30)
        
        response = db.table("sales").select("*").eq("user_id", user_id).gte("created_at", start_date_utc.isoformat()).execute()
        sales = [s for s in response.data if s.get("item_name") != "Payment Received"]
        
        for s in sales:
            created = s.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    ist_date = (dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                    
                    if ist_date in dates:
                        idx = dates.index(ist_date)
                        mode = s.get("payment_mode", "Cash")
                        price = s.get("total_price", 0)
                        if mode == "Udhaar":
                            weekly_data["udhaar"][idx] += price
                        else:
                            weekly_data["cash"][idx] += price
                except Exception:
                    pass
        
        # Round the final data
        weekly_data["cash"] = [round(v) for v in weekly_data["cash"]]
        weekly_data["udhaar"] = [round(v) for v in weekly_data["udhaar"]]
        
        return {"success": True, "data": weekly_data}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "message": str(e)}

@app.get("/sales/month")
async def get_monthly_sales(auth: tuple = Depends(get_user_client)):
    from datetime import datetime, timezone, timedelta
    try:
        db, user_id = auth
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        month_start = ist_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_start_utc = month_start - timedelta(hours=5, minutes=30)
        
        response = db.table("sales").select("*").eq("user_id", user_id).gte("created_at", month_start_utc.isoformat()).execute()
        all_sales = response.data
        sales = [s for s in all_sales if s.get("item_name") != "Payment Received"]
        
        daily_totals = {}
        for s in sales:
            created = s.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    ist_date = (dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                    if ist_date not in daily_totals:
                        daily_totals[ist_date] = {"revenue": 0, "orders": 0, "quantity": 0}
                    daily_totals[ist_date]["revenue"] += s.get("total_price", 0)
                    daily_totals[ist_date]["quantity"] += s.get("quantity", 0)
                except:
                    pass
        
        order_counts = {}
        for s in sales:
            created = s.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    ist_date = (dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                    order_key = created[:19]
                    if ist_date not in order_counts:
                        order_counts[ist_date] = set()
                    order_counts[ist_date].add(order_key)
                except:
                    pass
        
        for date, orders in order_counts.items():
            if date in daily_totals:
                daily_totals[date]["orders"] = len(orders)
        
        for s in sales:
            created = s.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    ist_date = (dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                    if ist_date in daily_totals:
                        current_profit = daily_totals[ist_date].get("profit", 0)
                        sale_profit = s.get("total_price", 0) - (s.get("total_cost", 0) or 0)
                        daily_totals[ist_date]["profit"] = current_profit + sale_profit
                except:
                    pass

        month_revenue = sum(s.get("total_price", 0) for s in sales)
        month_cost = sum(s.get("total_cost", 0) or 0 for s in sales)
        month_profit = month_revenue - month_cost
        month_quantity = sum(s.get("quantity", 0) for s in sales)
        month_orders = sum(d["orders"] for d in daily_totals.values())
        
        # Round values for display
        for d in daily_totals.values():
            d["revenue"] = round(d["revenue"], 2)
            d["quantity"] = round(d["quantity"], 2)
            d["profit"] = round(d.get("profit", 0), 2)
            
        return {
            "month": ist_now.strftime("%B %Y"),
            "month_revenue": round(month_revenue, 2),
            "month_profit": round(month_profit, 2),
            "month_orders": month_orders,
            "month_quantity": round(month_quantity, 2),
            "daily_totals": daily_totals
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Monthly Sales Error: {e}")
        return {"month": "", "month_revenue": 0, "month_orders": 0, "month_quantity": 0, "daily_totals": {}}

@app.get("/sales/date/{date}")
async def get_date_sales(date: str, auth: tuple = Depends(get_user_client)):
    from datetime import datetime, timezone, timedelta
    try:
        db, user_id = auth
        target_date = datetime.strptime(date, "%Y-%m-%d")
        ist_start = target_date.replace(hour=0, minute=0, second=0)
        ist_end = target_date.replace(hour=23, minute=59, second=59)
        utc_start = ist_start - timedelta(hours=5, minutes=30)
        utc_end = ist_end - timedelta(hours=5, minutes=30)
        
        response = db.table("sales").select("*").eq("user_id", user_id).gte("created_at", utc_start.isoformat()).lte("created_at", utc_end.isoformat()).execute()
        all_sales = response.data
        sales = [s for s in all_sales if s.get("item_name") != "Payment Received"]
        
        total_revenue = sum(s.get("total_price", 0) for s in sales)
        total_cost = sum(s.get("total_cost", 0) or 0 for s in sales)
        total_profit = total_revenue - total_cost
        total_quantity = sum(s.get("quantity", 0) for s in sales)
        
        item_summary = {}
        for s in sales:
            item = s.get("item_name", "Unknown")
            if item not in item_summary:
                item_summary[item] = 0
            item_summary[item] += s.get("quantity", 0)
        
        order_keys = set()
        for s in sales:
            created = s.get("created_at", "")
            if created:
                order_keys.add(created[:19])
        
        return {
            "date": date,
            "display_date": target_date.strftime("%d %b %Y"),
            "revenue": round(total_revenue, 2),
            "profit": round(total_profit, 2),
            "orders": len(order_keys),
            "quantity": round(total_quantity, 2),
            "items_sold": [{"name": k, "qty": round(v, 2)} for k, v in item_summary.items()]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Date Sales Error: {e}")
        return {"date": date, "display_date": date, "revenue": 0, "orders": 0, "quantity": 0, "items_sold": []}

# ============================================================================
# GUEST DEMO MODE — Simple, No Supabase Auth Required
# ============================================================================
# Demo mode uses GUEST_MAGIC_TOKEN (defined at top) + GUEST_USER_ID.
# No email/password, no service key, no Supabase Auth at all.
# Logged-in users get isolated data via their own user_id + RLS.
# ============================================================================


async def _seed_demo_inventory(client: Client, user_id: str):
    """Seeds demo inventory if empty."""
    try:
        res = client.table("inventory").select("id", count="exact").eq("user_id", user_id).limit(1).execute()
        count = res.count if res.count is not None else len(res.data)
        
        if count == 0:
            print("[demo] Seeding demo inventory...")
            now = datetime.now()
            items = [
                {"item_name": "Amul Milk",        "stock_quantity": 50,  "price": 30.0,  "cost_price": 25.0,  "expiry_date": (now + timedelta(days=5)).strftime('%Y-%m-%d'),   "user_id": user_id},
                {"item_name": "Maggi",             "stock_quantity": 100, "price": 14.0,  "cost_price": 11.0,  "expiry_date": (now + timedelta(days=180)).strftime('%Y-%m-%d'), "user_id": user_id},
                {"item_name": "Coca Cola",         "stock_quantity": 25,  "price": 40.0,  "cost_price": 32.0,  "expiry_date": (now + timedelta(days=365)).strftime('%Y-%m-%d'), "user_id": user_id},
                {"item_name": "Aashirvaad Atta",   "stock_quantity": 10,  "price": 450.0, "cost_price": 400.0, "expiry_date": (now + timedelta(days=60)).strftime('%Y-%m-%d'),  "user_id": user_id},
                {"item_name": "Britannia Bread",   "stock_quantity": 20,  "price": 50.0,  "cost_price": 40.0,  "expiry_date": (now + timedelta(days=3)).strftime('%Y-%m-%d'),   "user_id": user_id},
            ]
            client.table("inventory").insert(items).execute()
            print("[demo] ✅ Demo inventory seeded with 5 items!")
        else:
            print(f"[demo] Demo inventory already has {count} items — skipping seed.")
    except Exception as e:
        print(f"[demo] Inventory seed error: {e}")


@app.on_event("startup")
async def startup_event():
    """Seeds demo inventory at startup using anon client — no Supabase Auth needed."""
    print("--- Demo Mode Setup (No Auth Required) ---")
    client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    await _seed_demo_inventory(client, GUEST_USER_ID)
    print("[demo] ✅ Ready! Demo uses shared GUEST_USER_ID, logged-in users get isolated data.")


@app.post("/get-guest-token")
async def get_guest_token():
    """
    Zero-friction guest token. Returns the magic guest token that
    bypasses Supabase Auth entirely (RLS is disabled).
    Also seeds inventory if empty.
    """
    print("[get-guest-token] Returning guest magic token (auth bypass)")
    
    # Seed inventory if empty
    client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    await _seed_demo_inventory(client, GUEST_USER_ID)
    
    return {"access_token": GUEST_MAGIC_TOKEN}


@app.post("/reset-demo-inventory")
async def reset_demo_inventory():
    """
    Force-resets the demo user's inventory.
    Clears existing items and inserts fresh demo stock.
    Uses anon client directly (RLS disabled).
    """
    print("[reset-demo-inventory] Starting...")
    
    client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    user_id = GUEST_USER_ID
    
    # Clear existing inventory
    try:
        client.table("inventory").delete().eq("user_id", user_id).execute()
        print("[reset-demo-inventory] Cleared existing inventory")
    except Exception as e:
        print(f"[reset-demo-inventory] Clear error: {e}")
    
    # Insert fresh items
    try:
        now = datetime.now()
        items = [
            {"item_name": "Amul Milk",        "stock_quantity": 50,  "price": 30.0,  "cost_price": 25.0,  "expiry_date": (now + timedelta(days=5)).strftime('%Y-%m-%d'),   "user_id": user_id},
            {"item_name": "Maggi",             "stock_quantity": 100, "price": 14.0,  "cost_price": 11.0,  "expiry_date": (now + timedelta(days=180)).strftime('%Y-%m-%d'), "user_id": user_id},
            {"item_name": "Coca Cola",         "stock_quantity": 25,  "price": 40.0,  "cost_price": 32.0,  "expiry_date": (now + timedelta(days=365)).strftime('%Y-%m-%d'), "user_id": user_id},
            {"item_name": "Aashirvaad Atta",   "stock_quantity": 10,  "price": 450.0, "cost_price": 400.0, "expiry_date": (now + timedelta(days=60)).strftime('%Y-%m-%d'),  "user_id": user_id},
            {"item_name": "Britannia Bread",   "stock_quantity": 20,  "price": 50.0,  "cost_price": 40.0,  "expiry_date": (now + timedelta(days=3)).strftime('%Y-%m-%d'),   "user_id": user_id},
        ]
        client.table("inventory").insert(items).execute()
        print("[reset-demo-inventory] ✅ Inventory restocked!")
        return {"success": True, "message": "Inventory Restocked!", "items_added": len(items)}
    except Exception as e:
        print(f"[reset-demo-inventory] Insert error: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": f"Insert failed: {str(e)}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)