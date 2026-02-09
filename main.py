import os
import json
import re
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Global client for public operations only
supabase_anon: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

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
    Returns: (supabase_client, user_id)
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = authorization.replace("Bearer ", "")
    
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
        # Sort by stock_quantity ASC (Low stock first)
        response = db.table("inventory").select("*").eq("user_id", user_id).order("stock_quantity", desc=False).execute()
        return response.data
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Inventory Error: {e}")
        return []

@app.post("/chat")
async def chat_endpoint(req: ChatRequest, auth: tuple = Depends(get_user_client)):
    db, user_id = auth
    import json
    
    print(f"[CHAT] User {user_id} said: '{req.message}'")
    
    # Default prices for common items
    DEFAULT_PRICES = {
        "Milk": 60, "Bread": 40, "Eggs": 7, "Butter": 55, "Cheese": 100, "Paneer": 80, "Curd": 45,
        "Rice": 50, "Sugar": 45, "Salt": 25, "Flour": 35, "Wheat": 35, "Atta": 40, "Maida": 40, "Suji": 50, "Poha": 45,
        "Dal": 120, "Toor Dal": 140, "Moong Dal": 130, "Chana Dal": 90, "Urad Dal": 120, "Rajma": 150, "Chana": 80,
        "Tea": 250, "Coffee": 400,
        "Oil": 150, "Ghee": 550, "Mustard Oil": 180, "Turmeric": 200, "Red Chilli": 300, "Cumin": 350, "Coriander": 150,
        "Biscuits": 30, "Chips": 20, "Noodles": 15, "Soap": 40, "Detergent": 120, "Toothpaste": 80,
    }
    
    # Fetch user's inventory items dynamically
    user_inventory = {}
    try:
        inv_response = db.table("inventory").select("item_name, price, stock_quantity").eq("user_id", user_id).execute()
        user_inventory = {item['item_name']: item for item in inv_response.data} if inv_response.data else {}
        print(f"[CHAT] Fetched {len(user_inventory)} inventory items: {list(user_inventory.keys())}")
    except Exception as e:
        print(f"[CHAT] Error fetching inventory: {e}")
        import traceback
        traceback.print_exc()
    
    # Build ITEM_PRICES: merge default prices with user's custom items
    ITEM_PRICES = DEFAULT_PRICES.copy()
    for item_name, item_data in user_inventory.items():
        if item_name not in ITEM_PRICES:
            # Custom item - use stored price or default to 0
            ITEM_PRICES[item_name] = item_data.get('price') or 0
        elif item_data.get('price'):
            # User has set a custom price for a default item
            ITEM_PRICES[item_name] = item_data['price']
    
    available_items = list(ITEM_PRICES.keys())
    print(f"[CHAT] Total available items: {len(available_items)}, Custom items added: {[i for i in user_inventory.keys() if i not in DEFAULT_PRICES]}")
    
    WORD_TO_NUM = {
        'zero': 0, 'one': 1, 'won': 1, 'two': 2, 'too': 2, 'to': 2, 'tu': 2,
        'three': 3, 'tree': 3, 'free': 3, 'four': 4, 'for': 4, 'ford': 4, 'fore': 4,
        'five': 5, 'fife': 5, 'six': 6, 'sex': 6, 'sax': 6, 'seven': 7, 'saven': 7,
        'eight': 8, 'ate': 8, 'ait': 8, 'nine': 9, 'nain': 9, 'ten': 10, 'tan': 10,
        'eleven': 11, 'twelve': 12, 'half': 0.5, 'quarter': 0.25
    }
    
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
    }
    
    def fuzzy_match_item(word):
        word_lower = word.lower().strip()
        
        if word_lower in VOICE_TYPOS:
            word_lower = VOICE_TYPOS[word_lower].lower()
        
        # Exact match (case insensitive)
        for item in available_items:
            if item.lower() == word_lower:
                print(f"[MATCH] Exact match: '{word}' -> '{item}'")
                return item
        
        # Partial match
        for item in available_items:
            if item.lower() in word_lower or word_lower in item.lower():
                print(f"[MATCH] Partial match: '{word}' -> '{item}'")
                return item
        
        # Prefix match
        if len(word_lower) >= 3:
            for item in available_items:
                if item.lower().startswith(word_lower[:3]) or word_lower.startswith(item.lower()[:3]):
                    print(f"[MATCH] Prefix match: '{word}' -> '{item}'")
                    return item
        
        print(f"[MATCH] No match found for: '{word}'")
        return None
    
    def parse_message_locally(message):
        text = message.lower()
        
        # Expanded voice typos (Hindi + English phonetic errors)
        EXPANDED_VOICE_TYPOS = {
            **VOICE_TYPOS,
            # Hindi/Regional words
            'doodh': 'milk', 'dudh': 'milk', 'dudth': 'milk',
            'chawal': 'rice', 'chaawal': 'rice', 'chaval': 'rice',
            'cheeni': 'sugar', 'chini': 'sugar', 'shakkar': 'sugar',
            'aloo': 'potato', 'alu': 'potato', 'aaloo': 'potato',
            'pyaz': 'onion', 'pyaaz': 'onion', 'kanda': 'onion',
            'tamatar': 'tomato', 'tamater': 'tomato',
            'anda': 'eggs', 'ande': 'eggs', 'anday': 'eggs',
            'namak': 'salt', 'namkeen': 'salt',
            'tel': 'oil', 'teil': 'oil',
            'makhan': 'butter', 'makkhan': 'butter',
            'dahi': 'curd', 'dahee': 'curd',
            'roti': 'bread', 'rotee': 'bread',
            # Common voice recognition errors
            'sugarr': 'sugar', 'sugaar': 'sugar',
            'ricee': 'rice', 'ricerice': 'rice',
            'malak': 'milk', 'malk': 'milk', 'milkk': 'milk',
            'breads': 'bread', 'breadd': 'bread',
            'eggz': 'eggs', 'eg': 'eggs', 'eggg': 'eggs',
            'buttar': 'butter', 'butr': 'butter',
            'daal': 'dal', 'dhaal': 'dal', 'dhal': 'dal',
            'chees': 'cheese', 'cheez': 'cheese', 'cheeze': 'cheese',
            'panneer': 'paneer', 'pneer': 'paneer',
            'ataa': 'atta', 'aata': 'atta', 'aatta': 'atta',
            'maida': 'maida', 'mayda': 'maida',
            'biskut': 'biscuits', 'biscut': 'biscuits', 'biskoot': 'biscuits',
            'sabun': 'soap', 'saabun': 'soap',
            'maggi': 'noodles', 'maagi': 'noodles',
            'tooothpaste': 'toothpaste', 'toothpast': 'toothpaste', 'colgate': 'toothpaste',
            'condum': 'condom', 'condem': 'condom', 'kondom': 'condom',
        }
        
        # Remove common filler words
        text = re.sub(r'\b(sold|sell|sale|selling|please|and|the|a|an|some|of|also|give|add|more|i|want|need|get|me|us|becho|bech|do|de|dena|le|lo|lena|karo)\b', ' ', text)
        
        # Apply voice typos
        for typo, correction in EXPANDED_VOICE_TYPOS.items():
            text = re.sub(rf'\b{typo}\b', correction, text, flags=re.IGNORECASE)
        
        # Convert word numbers to digits (expanded)
        EXPANDED_WORD_TO_NUM = {
            **WORD_TO_NUM,
            'ek': 1, 'do': 2, 'teen': 3, 'char': 4, 'paanch': 5, 'panch': 5,
            'chhe': 6, 'chay': 6, 'saat': 7, 'aath': 8, 'nau': 9, 'das': 10,
            'gyarah': 11, 'barah': 12, 'terah': 13, 'chaudah': 14, 'pandrah': 15,
            'dhai': 2.5, 'adha': 0.5, 'dedh': 1.5, 'paune': 0.75,
            'double': 2, 'triple': 3, 'single': 1,
        }
        
        for word, num in EXPANDED_WORD_TO_NUM.items():
            text = re.sub(rf'\b{word}\b', str(num), text, flags=re.IGNORECASE)
        
        # Remove unit words (keep the number)
        text = re.sub(r'\bkg\b|\bkgs\b|\bkilo\b|\bkilos\b|\bkilogram\b|\bkilograms\b', ' ', text)
        text = re.sub(r'\bliters?\b|\bltrs?\b|\bml\b|\bltr\b', ' ', text)
        text = re.sub(r'\bpiece\b|\bpieces\b|\bpcs\b|\bunits?\b|\bpack\b|\bpacks\b|\bpacket\b|\bpackets\b', ' ', text)
        text = re.sub(r'\brunning\b|\brupees?\b|\brs\.?\b', ' ', text)
        
        # Clean up whitespace
        text = ' '.join(text.split())
        
        items_found = []
        used_positions = set()
        
        # Pattern 1: "2 milk" or "2.5 sugar" (number before item)
        pattern1 = r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)'
        for match in re.finditer(pattern1, text):
            qty_str, item_word = match.groups()
            matched_item = fuzzy_match_item(item_word)
            if matched_item and match.start() not in used_positions:
                items_found.append({"item": matched_item, "qty": float(qty_str)})
                used_positions.add(match.start())
        
        # Pattern 2: "milk 2" or "sugar 2.5" (item before number)
        pattern2 = r'([a-zA-Z]+)\s+(\d+(?:\.\d+)?)'
        for match in re.finditer(pattern2, text):
            item_word, qty_str = match.groups()
            matched_item = fuzzy_match_item(item_word)
            if matched_item and not any(i["item"] == matched_item for i in items_found):
                items_found.append({"item": matched_item, "qty": float(qty_str)})
        
        # Pattern 3: Standalone item names (default qty = 1)
        words = text.split()
        for word in words:
            if not word.replace('.', '').isdigit():
                matched_item = fuzzy_match_item(word)
                if matched_item and not any(i["item"] == matched_item for i in items_found):
                    items_found.append({"item": matched_item, "qty": 1.0})
        
        return items_found
    
    items_to_sell = parse_message_locally(req.message)
    
    if not items_to_sell:
        parse_prompt = f"""Parse this voice command for a grocery shop: "{req.message}"
Available items: {available_items}
Common errors: "four"/"for"/"Ford"→4, "too"/"to"→2, "keji"→kg
Return ONLY valid JSON array: [{{"item": "ItemName", "qty": number}}, ...]
If nothing found, return: []"""

        try:
            response = model.generate_content(parse_prompt)
            ai_response = response.text.strip()
            
            if ai_response.startswith("```"):
                ai_response = ai_response.split("```")[1]
                if ai_response.startswith("json"):
                    ai_response = ai_response[4:]
            ai_response = ai_response.strip()
            
            ai_items = json.loads(ai_response)
            
            for item_data in ai_items:
                item_name = item_data.get("item", "").strip().title()
                qty = float(item_data.get("qty", 1))
                
                matched_item = None
                for available in available_items:
                    if available.lower() == item_name.lower():
                        matched_item = available
                        break
                
                if matched_item and qty > 0:
                    items_to_sell.append({"item": matched_item, "qty": qty})
                    
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"AI Parse Error (using local only): {e}")
    
    if not items_to_sell:
        return {"message": "❌ I didn't understand that.", "warning": "Try: 'Sold 2kg Sugar, 9kg Flour'"}
    
    # Generate a unique transaction_id for this order (groups all items from same command)
    import uuid
    transaction_id = str(uuid.uuid4())[:8]  # Short 8-char ID like "a1b2c3d4"
    
    results = []
    total_price = 0
    failed = []
    
    for sale in items_to_sell:
        item = sale["item"].strip().title()  # Normalize to Title Case for consistency
        qty = sale["qty"]
        
        # Case-insensitive lookup using .ilike() for Supabase or by using the normalized name
        inv_check = db.table("inventory").select("stock_quantity, item_name").eq("user_id", user_id).eq("item_name", item).execute()
        
        # If exact match not found, try to find case-insensitive match from user_inventory
        if not inv_check.data and user_inventory:
            for inv_name, inv_data in user_inventory.items():
                if inv_name.lower() == item.lower():
                    item = inv_name  # Use the actual stored name
                    inv_check = db.table("inventory").select("stock_quantity, item_name").eq("user_id", user_id).eq("item_name", item).execute()
                    break
        
        current_stock = inv_check.data[0]['stock_quantity'] if inv_check.data else 0
        
        if current_stock < qty:
            failed.append(f"{item} (only {current_stock} left)")
            continue
        
        unit_price = ITEM_PRICES.get(item, 0)
        price = qty * unit_price
        total_price += price
        
        new_stock = current_stock - qty
        if inv_check.data:
            db.table("inventory").update({"stock_quantity": new_stock}).eq("user_id", user_id).eq("item_name", item).execute()
        else:
            db.table("inventory").insert({"item_name": item, "stock_quantity": -qty, "user_id": user_id}).execute()
        
        db.table("sales").insert({
            "item_name": item,
            "quantity": qty,
            "total_price": price,
            "customer_name": "Walk-in",
            "user_id": user_id,
            "transaction_id": transaction_id  # Groups all items from same voice command
        }).execute()
        
        results.append(f"{qty} {item}")
    
    if results and failed:
        msg = f"✅ Sold {', '.join(results)} for ₹{round(total_price, 2)} | ❌ Failed: {', '.join(failed)}"
        return {"message": msg, "warning": "Some items failed", "success": True}
    elif results:
        msg = f"✅ Sold {', '.join(results)} for ₹{round(total_price, 2)}"
        return {"message": msg, "warning": None, "success": True}
    else:
        return {"message": f"❌ FAILED: {', '.join(failed)}", "warning": "Insufficient Stock", "success": False}

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
                    "total_price": 0,
                    "order_id": oid,
                    "time": time_str
                }
            orders[oid]["items"].append(f"{s.get('quantity', 0)} {s.get('item_name', '?')}")
            orders[oid]["total_price"] += s.get("total_price", 0)
        
        transactions = []
        order_num = len(orders)
        for oid, data in orders.items():
            transactions.append({
                "order": f"{order_num:04d}",
                "item": ", ".join(data["items"]),
                "qty": len(data["items"]),
                "price": round(data["total_price"], 2),
                "time": data["time"]
            })
            order_num -= 1
        
        from datetime import timedelta
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        today_date = ist_now.strftime("%d %b %Y")
        
        return {
            "date": today_date,
            "total_revenue": round(total_revenue, 2),
            "total_sales": len(orders),
            "total_quantity": round(total_quantity, 2),
            "items_sold": [{"name": k, "qty": round(v, 2)} for k, v in item_summary.items()],
            "transactions": transactions[:10]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Sales Error: {e}")
        return {"total_revenue": 0, "total_sales": 0, "total_quantity": 0, "items_sold": [], "transactions": []}

class AddStockRequest(BaseModel):
    item_name: str
    quantity: float
    price: Optional[float] = None

@app.post("/add-stock")
async def add_stock(req: AddStockRequest, auth: tuple = Depends(get_user_client)):
    try:
        db, user_id = auth
        item = req.item_name.strip().title()
        qty = req.quantity
        
        inv_check = db.table("inventory").select("stock_quantity, price").eq("user_id", user_id).eq("item_name", item).execute()
        
        if inv_check.data:
            current = inv_check.data[0]['stock_quantity']
            new_stock = current + qty
            update_data = {"stock_quantity": new_stock}
            if req.price is not None:
                update_data["price"] = req.price
            db.table("inventory").update(update_data).eq("user_id", user_id).eq("item_name", item).execute()
            return {"message": f"✅ Added {qty} to {item}. New stock: {new_stock}", "success": True}
        else:
            insert_data = {"item_name": item, "stock_quantity": qty, "user_id": user_id}
            if req.price is not None:
                insert_data["price"] = req.price
            db.table("inventory").insert(insert_data).execute()
            return {"message": f"✅ Created {item} with stock: {qty}", "success": True}
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Add Stock Error: {e}")
        return {"message": f"❌ Error: {str(e)}", "success": False}

@app.get("/sales/month")
async def get_monthly_sales(auth: tuple = Depends(get_user_client)):
    from datetime import datetime, timezone, timedelta
    try:
        db, user_id = auth
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        month_start = ist_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_start_utc = month_start - timedelta(hours=5, minutes=30)
        
        response = db.table("sales").select("*").eq("user_id", user_id).gte("created_at", month_start_utc.isoformat()).execute()
        sales = response.data
        
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
        
        # Calculate totals directly from sales (not from daily_totals) to avoid any rounding/grouping issues
        month_revenue = sum(s.get("total_price", 0) for s in sales)
        month_quantity = sum(s.get("quantity", 0) for s in sales)
        month_orders = sum(d["orders"] for d in daily_totals.values())
        
        return {
            "month": ist_now.strftime("%B %Y"),
            "month_revenue": round(month_revenue, 2),
            "month_orders": month_orders,
            "month_quantity": round(month_quantity, 2),
            "daily_totals": {k: {"revenue": round(v["revenue"], 2), "orders": v["orders"], "quantity": round(v["quantity"], 2)} for k, v in daily_totals.items()}
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
        sales = response.data
        
        total_revenue = sum(s.get("total_price", 0) for s in sales)
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
            "orders": len(order_keys),
            "quantity": round(total_quantity, 2),
            "items_sold": [{"name": k, "qty": round(v, 2)} for k, v in item_summary.items()]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Date Sales Error: {e}")
        return {"date": date, "display_date": date, "revenue": 0, "orders": 0, "quantity": 0, "items_sold": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)