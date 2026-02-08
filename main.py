import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
import google.generativeai as genai

load_dotenv()

# --- CONFIGURATION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

class ChatRequest(BaseModel):
    message: str

# --- ROUTES ---
@app.get("/inventory")
async def get_inventory():
    """Fetches all inventory items sorted by name for the dashboard."""
    try:
        response = supabase.table("inventory").select("*").order("item_name").execute()
        return response.data
    except Exception as e:
        print(f"Inventory Error: {e}")
        return []

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    import re
    import json
    
    # Fixed Prices (per unit/kg) - based on Indian market rates
    ITEM_PRICES = {
        "Milk": 60, "Bread": 40, "Eggs": 7, "Butter": 55, "Cheese": 100, "Paneer": 80, "Curd": 45,
        "Rice": 50, "Sugar": 45, "Salt": 25, "Flour": 35, "Wheat": 35, "Atta": 40, "Maida": 40, "Suji": 50, "Poha": 45,
        "Dal": 120, "Toor Dal": 140, "Moong Dal": 130, "Chana Dal": 90, "Urad Dal": 120, "Rajma": 150, "Chana": 80,
        "Tea": 250, "Coffee": 400,
        "Oil": 150, "Ghee": 550, "Mustard Oil": 180, "Turmeric": 200, "Red Chilli": 300, "Cumin": 350, "Coriander": 150,
        "Biscuits": 30, "Chips": 20, "Noodles": 15, "Soap": 40, "Detergent": 120, "Toothpaste": 80,
    }
    
    available_items = list(ITEM_PRICES.keys())
    
    # ============== SMART LOCAL PARSER ==============
    # Converts word numbers and common voice errors
    WORD_TO_NUM = {
        'zero': 0, 'one': 1, 'won': 1, 'two': 2, 'too': 2, 'to': 2, 'tu': 2,
        'three': 3, 'tree': 3, 'free': 3, 'four': 4, 'for': 4, 'ford': 4, 'fore': 4,
        'five': 5, 'fife': 5, 'six': 6, 'sex': 6, 'sax': 6, 'seven': 7, 'saven': 7,
        'eight': 8, 'ate': 8, 'ait': 8, 'nine': 9, 'nain': 9, 'ten': 10, 'tan': 10,
        'eleven': 11, 'twelve': 12, 'half': 0.5, 'quarter': 0.25
    }
    
    # Common voice typos mapping
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
        """Find the closest matching item in inventory"""
        word_lower = word.lower().strip()
        
        # Check voice typos first
        if word_lower in VOICE_TYPOS:
            word_lower = VOICE_TYPOS[word_lower].lower()
        
        # Exact match
        for item in available_items:
            if item.lower() == word_lower:
                return item
        
        # Partial match (item contains word or word contains item)
        for item in available_items:
            if item.lower() in word_lower or word_lower in item.lower():
                return item
        
        # Similarity match (first 3+ characters)
        if len(word_lower) >= 3:
            for item in available_items:
                if item.lower().startswith(word_lower[:3]) or word_lower.startswith(item.lower()[:3]):
                    return item
        
        return None
    
    def parse_message_locally(message):
        """Parse voice message with smart number and item detection"""
        text = message.lower()
        
        # Remove common filler words
        text = re.sub(r'\b(sold|sell|sale|sold|selling|please|and|the|a|an|some|of|also|give|add|more)\b', ' ', text)
        
        # Apply voice typo corrections
        for typo, correction in VOICE_TYPOS.items():
            text = re.sub(rf'\b{typo}\b', correction, text, flags=re.IGNORECASE)
        
        # Convert word numbers to digits
        for word, num in WORD_TO_NUM.items():
            text = re.sub(rf'\b{word}\b', str(num), text, flags=re.IGNORECASE)
        
        # Remove 'kg' and similar units (they just indicate quantity)
        text = re.sub(r'\bkg\b|\bkgs\b|\bkilo\b|\bkilos\b|\bkilogram\b|\bkilograms\b', ' ', text)
        text = re.sub(r'\bliters?\b|\bltrs?\b|\bml\b', ' ', text)
        text = re.sub(r'\bpiece\b|\bpieces\b|\bpcs\b|\bunits?\b', ' ', text)
        
        # Clean up whitespace
        text = ' '.join(text.split())
        
        items_found = []
        
        # Pattern 1: "NUMBER ITEM" (e.g., "4 rice", "2 sugar")
        pattern1 = r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)'
        matches1 = re.findall(pattern1, text)
        
        for qty_str, item_word in matches1:
            matched_item = fuzzy_match_item(item_word)
            if matched_item:
                items_found.append({"item": matched_item, "qty": float(qty_str)})
        
        # Pattern 2: "ITEM NUMBER" (e.g., "rice 4")
        pattern2 = r'([a-zA-Z]+)\s+(\d+(?:\.\d+)?)'
        matches2 = re.findall(pattern2, text)
        
        for item_word, qty_str in matches2:
            matched_item = fuzzy_match_item(item_word)
            if matched_item:
                # Avoid duplicates
                if not any(i["item"] == matched_item for i in items_found):
                    items_found.append({"item": matched_item, "qty": float(qty_str)})
        
        # Pattern 3: Just item names without quantity (assume 1)
        words = text.split()
        for word in words:
            if not word.isdigit():
                matched_item = fuzzy_match_item(word)
                if matched_item and not any(i["item"] == matched_item for i in items_found):
                    items_found.append({"item": matched_item, "qty": 1.0})
        
        return items_found
    
    # Try local parsing first (fast & doesn't use API quota)
    items_to_sell = parse_message_locally(req.message)
    
    # If local parsing found items, use them; otherwise try AI
    if not items_to_sell:
        # Try Gemini AI as fallback
        parse_prompt = f"""Parse this voice command for a grocery shop: "{req.message}"
Available items: {available_items}
Common errors: "four"/"for"/"Ford"→4, "too"/"to"→2, "keji"→kg
Return ONLY valid JSON array: [{{"item": "ItemName", "qty": number}}, ...]
If nothing found, return: []"""

        try:
            response = model.generate_content(parse_prompt)
            ai_response = response.text.strip()
            
            # Clean up response
            if ai_response.startswith("```"):
                ai_response = ai_response.split("```")[1]
                if ai_response.startswith("json"):
                    ai_response = ai_response[4:]
            ai_response = ai_response.strip()
            
            ai_items = json.loads(ai_response)
            
            # Validate AI items
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
            print(f"AI Parse Error (using local only): {e}")
    
    if not items_to_sell:
        return {"message": "❌ I didn't understand that.", "warning": "Try: 'Sold 2kg Sugar, 9kg Flour'"}
    
    # Process each item
    results = []
    total_price = 0
    failed = []
    
    for sale in items_to_sell:
        item = sale["item"]
        qty = sale["qty"]
        
        # Check stock
        inv_check = supabase.table("inventory").select("stock_quantity").eq("item_name", item).execute()
        current_stock = inv_check.data[0]['stock_quantity'] if inv_check.data else 0
        
        if current_stock < qty:
            failed.append(f"{item} (only {current_stock} left)")
            continue
        
        # Calculate price
        unit_price = ITEM_PRICES.get(item, 0)
        price = qty * unit_price
        total_price += price
        
        # Update inventory
        new_stock = current_stock - qty
        if inv_check.data:
            supabase.table("inventory").update({"stock_quantity": new_stock}).eq("item_name", item).execute()
        else:
            supabase.table("inventory").insert({"item_name": item, "stock_quantity": -qty}).execute()
        
        # Insert sale
        supabase.table("sales").insert({
            "item_name": item,
            "quantity": qty,
            "total_price": price,
            "customer_name": "Walk-in"
        }).execute()
        
        results.append(f"{qty} {item}")
    
    # Build response
    if results and failed:
        msg = f"✅ Sold {', '.join(results)} for ₹{round(total_price, 2)} | ❌ Failed: {', '.join(failed)}"
        return {"message": msg, "warning": "Some items failed", "success": True}
    elif results:
        msg = f"✅ Sold {', '.join(results)} for ₹{round(total_price, 2)}"
        return {"message": msg, "warning": None, "success": True}
    else:
        return {"message": f"❌ FAILED: {', '.join(failed)}", "warning": "Insufficient Stock", "success": False}

@app.get("/sales/today")
async def get_todays_sales():
    """Returns today's sales summary for the analysis panel."""
    from datetime import datetime, timezone
    try:
        # Get today's date in ISO format
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        
        # Fetch sales from today
        response = supabase.table("sales").select("*").gte("created_at", today).execute()
        sales = response.data
        
        # Calculate summary
        total_revenue = sum(s.get('total_price', 0) for s in sales)
        total_quantity = sum(s.get('quantity', 0) for s in sales)  # Sum of all quantities
        total_sales = len(sales)  # Count of transactions (each sale = 1 item)
        
        # Group by item for summary
        item_summary = {}
        for s in sales:
            item = s.get('item_name', 'Unknown')
            item_summary[item] = item_summary.get(item, 0) + s.get('quantity', 0)
        
        # Group transactions by order_id (multi-item orders shown together)
        # Sales without order_id are treated as individual orders
        from collections import OrderedDict
        orders = OrderedDict()
        legacy_counter = 0
        
        for s in reversed(sales):  # Most recent first
            # Group by exact timestamp (seconds) - items inserted within same second are same order
            created = s.get("created_at", "")
            if created:
                # Use timestamp up to seconds: "2026-02-08T12:25:30"
                oid = created[:19]
            else:
                legacy_counter += 1
                oid = f"LEGACY_{legacy_counter}"
            
            if oid not in orders:
                # Parse timestamp and convert to IST
                time_str = ""
                if created:
                    try:
                        from datetime import timedelta
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        # Convert UTC to IST (UTC + 5:30)
                        ist = dt + timedelta(hours=5, minutes=30)
                        time_str = ist.strftime("%I:%M %p")  # e.g., "05:55 PM"
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
        
        # Convert to list format for frontend
        transactions = []
        order_num = len(orders)
        for oid, data in orders.items():
            transactions.append({
                "order": f"{order_num:04d}",  # Sequential order number
                "item": ", ".join(data["items"]),
                "qty": len(data["items"]),
                "price": round(data["total_price"], 2),
                "time": data["time"]
            })
            order_num -= 1
        
        # Get today's date in IST
        from datetime import timedelta
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        today_date = ist_now.strftime("%d %b %Y")  # e.g., "08 Feb 2026"
        
        return {
            "date": today_date,
            "total_revenue": round(total_revenue, 2),
            "total_sales": len(orders),
            "total_quantity": round(total_quantity, 2),
            "items_sold": [{"name": k, "qty": round(v, 2)} for k, v in item_summary.items()],
            "transactions": transactions[:10]
        }
    except Exception as e:
        print(f"Sales Error: {e}")
        return {"total_revenue": 0, "total_sales": 0, "total_quantity": 0, "items_sold": [], "transactions": []}

class AddStockRequest(BaseModel):
    item_name: str
    quantity: float

@app.post("/add-stock")
async def add_stock(req: AddStockRequest):
    """Add new item or increase existing stock."""
    try:
        item = req.item_name.strip().title()
        qty = req.quantity
        
        # Check if item exists
        inv_check = supabase.table("inventory").select("stock_quantity").eq("item_name", item).execute()
        
        if inv_check.data:
            # Update existing
            current = inv_check.data[0]['stock_quantity']
            new_stock = current + qty
            supabase.table("inventory").update({"stock_quantity": new_stock}).eq("item_name", item).execute()
            return {"message": f"✅ Added {qty} to {item}. New stock: {new_stock}", "success": True}
        else:
            # Insert new
            supabase.table("inventory").insert({"item_name": item, "stock_quantity": qty}).execute()
            return {"message": f"✅ Created {item} with stock: {qty}", "success": True}
    except Exception as e:
        print(f"Add Stock Error: {e}")
        return {"message": f"❌ Error: {str(e)}", "success": False}

@app.get("/sales/month")
async def get_monthly_sales():
    """Get daily sales totals for the current month."""
    from datetime import datetime, timezone, timedelta
    try:
        # Get current month start in IST
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        month_start = ist_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Convert back to UTC for query
        month_start_utc = month_start - timedelta(hours=5, minutes=30)
        
        # Fetch all sales for this month
        response = supabase.table("sales").select("*").gte("created_at", month_start_utc.isoformat()).execute()
        sales = response.data
        
        # Group by date (IST)
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
        
        # Count unique orders per day (by timestamp seconds)
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
        
        # Calculate month total
        month_revenue = sum(d["revenue"] for d in daily_totals.values())
        month_orders = sum(d["orders"] for d in daily_totals.values())
        month_quantity = sum(d["quantity"] for d in daily_totals.values())
        
        return {
            "month": ist_now.strftime("%B %Y"),
            "month_revenue": round(month_revenue, 2),
            "month_orders": month_orders,
            "month_quantity": round(month_quantity, 2),
            "daily_totals": {k: {"revenue": round(v["revenue"], 2), "orders": v["orders"], "quantity": round(v["quantity"], 2)} for k, v in daily_totals.items()}
        }
    except Exception as e:
        print(f"Monthly Sales Error: {e}")
        return {"month": "", "month_revenue": 0, "month_orders": 0, "month_quantity": 0, "daily_totals": {}}

@app.get("/sales/date/{date}")
async def get_date_sales(date: str):
    """Get detailed sales for a specific date (format: YYYY-MM-DD)."""
    from datetime import datetime, timezone, timedelta
    try:
        # Parse date and get UTC range
        target_date = datetime.strptime(date, "%Y-%m-%d")
        # IST start of day -> UTC
        ist_start = target_date.replace(hour=0, minute=0, second=0)
        ist_end = target_date.replace(hour=23, minute=59, second=59)
        utc_start = ist_start - timedelta(hours=5, minutes=30)
        utc_end = ist_end - timedelta(hours=5, minutes=30)
        
        # Fetch sales for this date
        response = supabase.table("sales").select("*").gte("created_at", utc_start.isoformat()).lte("created_at", utc_end.isoformat()).execute()
        sales = response.data
        
        # Calculate totals
        total_revenue = sum(s.get("total_price", 0) for s in sales)
        total_quantity = sum(s.get("quantity", 0) for s in sales)
        
        # Group by item
        item_summary = {}
        for s in sales:
            item = s.get("item_name", "Unknown")
            if item not in item_summary:
                item_summary[item] = 0
            item_summary[item] += s.get("quantity", 0)
        
        # Count orders
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
        print(f"Date Sales Error: {e}")
        return {"date": date, "display_date": date, "revenue": 0, "orders": 0, "quantity": 0, "items_sold": []}