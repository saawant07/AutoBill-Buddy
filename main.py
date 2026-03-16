import os
import json
import re
import difflib
import logging
from deep_translator import GoogleTranslator
from datetime import datetime, timedelta
import asyncio
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from supabase import create_client, Client
import google.generativeai as genai
import time
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Global client for public operations only
supabase_anon: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# Guest mode bypass — loaded from env for security
GUEST_MAGIC_TOKEN = os.getenv("GUEST_MAGIC_TOKEN", "GUEST_MODE_NO_AUTH")
GUEST_USER_ID = os.getenv("GUEST_USER_ID", "933fc862-30f9-45ef-b83f-c9d57f1ebfc6")

app = FastAPI()

# CORS — restrict to known origins
_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://localhost:3000",
    "https://autobillbuddy.vercel.app",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

# /config endpoint REMOVED — anon key no longer exposed to frontend
# ============================================================================
# RATE LIMITER & MIDDLEWARES
# ============================================================================
_rate_buckets = defaultdict(lambda: {"tokens": 10, "last": time.time()})

# Endpoint-specific rate limits (requests per minute)
_RATE_LIMITS = {
    "/parse-order": 20,
    "/confirm-order": 10,
    "/get-guest-token": 5,
    "/add-stock": 30,
    "/dues/settle": 10,
    "/inventory": 60,
    "/prices": 60,
    "/dues": 60,
    "/dues/{customer_name}": 60,
    "/sales/today": 60,
    "/sales/month": 60,
    "/sales/date/{date}": 60,
    "/sales/year": 60,
    "/analytics/weekly": 60,
    "/reduce-stock": 30,
    "/delete-item": 10,
    "/reset-demo-inventory": 5,
}
_DEFAULT_RATE_LIMIT = 30  # Default for other endpoints
_DEFAULT_RATE_WINDOW = 60  # per 60 seconds

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 1. Request Size Limit (10KB max for POST inputs to prevent large payloads)
    if request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10_000:
            return Response(status_code=413, content="Request body too large")
            
    # Process request
    response = await call_next(request)
    
    # 2. Security Headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    
    return response

def check_rate_limit(request: Request):
    """
    Check rate limit for the request.
    """
    # Determine rate limit based on endpoint
    path = request.url.path
    rate_limit = _RATE_LIMITS.get(path, _DEFAULT_RATE_LIMIT)

    # Use IP + path as the bucket key for per-endpoint limiting
    ip = request.client.host if request.client else "unknown"
    bucket_key = f"{ip}:{path}"
    bucket = _rate_buckets[bucket_key]

    now = time.time()
    # Refill tokens based on time elapsed
    elapsed = now - bucket["last"]
    bucket["tokens"] = min(float(rate_limit), bucket["tokens"] + elapsed * (rate_limit / _DEFAULT_RATE_WINDOW))
    bucket["last"] = now
    if bucket["tokens"] < 1:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded for {path}. Please slow down.")
    bucket["tokens"] -= 1

# ============================================================================
# SUPABASE PROXY (Bypass Indian ISP Block)
# ============================================================================
@app.api_route("/supabase-proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def supabase_proxy(path: str, request: Request):
    check_rate_limit(request)
    if not SUPABASE_URL:
        return Response(status_code=500, content="SUPABASE_URL not configured")

    # Construct the target URL
    target_url = f"{SUPABASE_URL.rstrip('/')}/{path}"
    
    # Forward all query params
    query_string = request.url.query.encode("utf-8")
    if query_string:
        target_url = f"{target_url}?{query_string.decode('utf-8')}"

    # Prepare headers: forward all except host to avoid SNI/host mismatches
    headers = dict(request.headers)
    headers.pop("host", None)
    
    # Read the body
    body = await request.body()
    
    async def request_streamer():
        async with httpx.AsyncClient() as client:
            try:
                # Use stream to handle streaming responses if any
                async with client.stream(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                    timeout=30.0 # generous timeout for proxy
                ) as response:
                    
                    # Prepare response headers, carefully removing hop-by-hop headers
                    response_headers = dict(response.headers)
                    hop_by_hop = ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade']
                    for h in hop_by_hop:
                        response_headers.pop(h, None)
                    
                    # Yield chunks as they arrive
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except httpx.RequestError as e:
                # In case of connection failure from proxy to Supabase
                print(f"Proxy request error to {target_url}: {e}")
                raise HTTPException(status_code=502, detail="Bad Gateway: Error connecting to Supabase from Proxy")

    # If it's a small short-lived request we can just use normal Response, but StreamingResponse is safer for long-running or large payloads
    # Let's use a simpler regular Request for the proxy to avoid hanging issues with StreamingResponse in some FastAPI setups if we dont really need it
    
    async with httpx.AsyncClient() as client:
        try:
            proxy_response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                timeout=30.0
            )
            
            # Filter response headers
            response_headers = dict(proxy_response.headers)
            hop_by_hop = ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade', 'content-encoding', 'content-length']
            for h in hop_by_hop:
                response_headers.pop(h, None)
                
            return Response(
                content=proxy_response.content,
                status_code=proxy_response.status_code,
                headers=response_headers,
                media_type=proxy_response.headers.get("content-type")
            )
            
        except httpx.RequestError as e:
            print(f"Proxy connection error: {e}")
            return Response(status_code=502, content=f"Proxy error connecting to Supabase: {str(e)}")

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
async def get_inventory(request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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
async def get_all_prices(request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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

# ============================================================================
# MULTILINGUAL ALIAS GENERATOR — Hindi, Bengali, Hinglish + Phonetic Variations
# ============================================================================
def generate_multilingual_aliases(word):
    word = word.lower().strip()
    aliases = set([word])
    
    try:
        # 1. Fetch Native Scripts
        hindi_word = GoogleTranslator(source='en', target='hi').translate(word)
        bengali_word = GoogleTranslator(source='en', target='bn').translate(word)
        
        if hindi_word: aliases.add(hindi_word)
        if bengali_word: aliases.add(bengali_word)
        
        # 2. Hardcoded common Kirana Hinglish mappings (Safety Net for Romanized Speech-to-Text)
        kirana_dict = {
            "milk": ["doodh", "dudh"],
            "sugar": ["cheeni", "chini", "shakkar"],
            "water": ["pani", "jol"],
            "rice": ["chawal", "chal"],
            "wheat": ["atta", "gehu"],
            "salt": ["namak", "noon"],
            "tea": ["chai", "cha"],
            "potato": ["aloo", "alu"],
            "onion": ["pyaaz", "piyaj"]
        }
        
        if word in kirana_dict:
            aliases.update(kirana_dict[word])
            
    except Exception as e:
        print(f"Translation failed for {word}: {e}")
        
    # Return as comma-separated string, limited to 10 variations to keep DB clean
    return ",".join(list(aliases)[:10])


# ============================================================================
# STEP 4: FUZZY MATCHING — Searches BOTH inventory names AND DB aliases
# ============================================================================
def fuzzy_match_item(word, available_items, custom_aliases=None):
    VOICE_TYPOS = {
        'keji': 'kg', 'kaji': 'kg', 'kilo': 'kg', 'kilos': 'kg', 'kilogram': 'kg',
        'rise': 'rice', 'rais': 'rice', 'raice': 'rice',
        'tee': 'tea', 'chai': 'tea',
        'melk': 'milk', 'melku': 'milk',
        'suger': 'sugar', 'sugur': 'sugar',
        'flor': 'flour', 'flower': 'flour',
        'bred': 'bread', 'brad': 'bread',
        'ags': 'eggs', 'aggs': 'eggs',
        'ghea': 'ghee', 'ghi': 'ghee',
        'panir': 'paneer', 'paner': 'paneer',
        'coffe': 'coffee', 'koffee': 'coffee', 'cofee': 'coffee',
        'biskit': 'biscuits', 'biscuit': 'biscuits',
        'chiips': 'chips', 'chip': 'chips',
        'noodle': 'noodles',
        'maggie': 'maggi', 'maagi': 'maggi',
    }
    
    if custom_aliases is None:
        custom_aliases = {}
    
    word_lower = word.lower().strip()
    if word_lower in VOICE_TYPOS:
        word_lower = VOICE_TYPOS[word_lower].lower()
    
    # Check DB aliases (exact match: alias -> item_name)
    if word_lower in custom_aliases:
        mapped_name = custom_aliases[word_lower]
        for item in available_items:
            if item.lower() == mapped_name.lower():
                return item
    
    # Exact match against inventory names
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

    # Token-based fuzzy match (matches typoed single words against multi-word inventory items)
    if len(word_lower) >= 3:
        import difflib
        for item in available_items:
            item_words = item.lower().split()
            for iw in item_words:
                if len(iw) >= 3:
                    if difflib.SequenceMatcher(None, word_lower, iw).ratio() >= 0.7:
                        return item
    
    # difflib fuzzy fallback — search BOTH item names AND alias strings
    if len(word_lower) >= 3:
        import difflib
        inventory_lower = [it.lower() for it in available_items]
        alias_strings = list(custom_aliases.keys())
        all_candidates = inventory_lower + alias_strings
        
        matches = difflib.get_close_matches(word_lower, all_candidates, n=1, cutoff=0.6)
        if matches:
            match = matches[0]
            # Check if matched an inventory name
            for item in available_items:
                if item.lower() == match:
                    return item
            # Check if matched an alias — resolve to actual item
            if match in custom_aliases:
                mapped_name = custom_aliases[match]
                for item in available_items:
                    if item.lower() == mapped_name.lower():
                        return item
    return None

def parse_message_locally(message, available_items, custom_aliases=None):
    text = message.lower()
    
    if custom_aliases is None:
        custom_aliases = {}
    
    # ── Devanagari Hindi → Romanized conversion ──────────────────────
    # When speech recognition uses hi-IN, it outputs Devanagari script.
    # Convert to romanized Hindi so existing dictionaries can match.
    HINDI_TO_ROMAN = {
        # Numbers
        'एक': 'ek', 'दो': 'do', 'तीन': 'teen', 'चार': 'char', 'पांच': 'panch',
        'छह': 'chhe', 'छे': 'chhe', 'सात': 'saat', 'आठ': 'aath', 'नौ': 'nau',
        'दस': 'das', 'ग्यारह': 'gyarah', 'बारह': 'barah', 'तेरह': 'terah',
        'चौदह': 'chaudah', 'पंद्रह': 'pandrah', 'सोलह': 'solah',
        'बीस': 'bees', 'तीस': 'tees', 'चालीस': 'chalees', 'पचास': 'pachas',
        'सौ': 'sau', 'सो': 'sau',
        # Fractions
        'आधा': 'adha', 'आधी': 'adhi', 'ढाई': 'dhai', 'डेढ़': 'dedh',
        'पौने': 'paune', 'सवा': 'sawa', 'पाव': 'pav',
        # Units
        'किलो': 'kilo', 'किलोग्राम': 'kilogram', 'लीटर': 'liter',
        'पैकेट': 'packet', 'पैक': 'pack', 'बोतल': 'bottle',
        'केजी': 'kg', 'ग्राम': 'gram',
        # Common grocery items
        'दूध': 'doodh', 'चावल': 'chawal', 'चीनी': 'cheeni',
        'आलू': 'aloo', 'प्याज': 'pyaz', 'टमाटर': 'tamatar',
        'अंडा': 'anda', 'अंडे': 'ande', 'नमक': 'namak',
        'तेल': 'tel', 'मक्खन': 'makkhan', 'दही': 'dahi',
        'रोटी': 'roti', 'दाल': 'daal', 'चना': 'chana',
        'पनीर': 'panneer', 'आटा': 'atta', 'मैदा': 'maida',
        'चाय': 'chai', 'कॉफी': 'coffee', 'साबुन': 'sabun',
        'शक्कर': 'shakkar', 'गेहूं': 'wheat', 'बेसन': 'besan',
        'हल्दी': 'haldi', 'मिर्च': 'mirch', 'धनिया': 'dhaniya',
        'जीरा': 'jeera', 'राई': 'rai', 'सरसों': 'sarson',
        'घी': 'ghee', 'छाछ': 'chaach', 'लस्सी': 'lassi',
        'बिस्किट': 'biscuit', 'मैगी': 'maggi', 'नूडल्स': 'noodles',
        'ब्रेड': 'bread', 'शुगर': 'sugar', 'सुगर': 'sugar',
        'मिल्क': 'milk', 'राइस': 'rice', 'ऑयल': 'oil',
        'बटर': 'butter', 'ब्लैंकेट': 'blanket',
        # Connectors (replace with space)
        'और': 'aur', 'का': 'ka', 'की': 'ki', 'के': 'ke',
        'दे': 'de', 'दो।': 'do', 'दीजिए': 'dijiye', 'चाहिए': 'chahiye',
        'वाला': 'wala', 'वाली': 'wali',
    }
    
    # Apply Devanagari conversion
    for hindi, roman in HINDI_TO_ROMAN.items():
        text = text.replace(hindi, roman)
    
    # Typos and Numbers dictionaries
    WORD_TO_NUM = {
        # ── English Numbers + Common STT Mishearings ──────────────────
        'zero': 0, 'nil': 0, 'nill': 0, 'nothing': 0,
        'one': 1, 'won': 1, 'wan': 1, 'wun': 1, 'on': 1, 'onne': 1,
        'two': 2, 'too': 2, 'to': 2, 'tu': 2, 'tow': 2, 'tuo': 2, 'twoo': 2,
        'three': 3, 'tree': 3, 'free': 3, 'thee': 3, 'thre': 3, 'tri': 3, 'thr': 3,
        'four': 4, 'for': 4, 'fore': 4, 'ford': 4, 'phor': 4, 'foor': 4, 'fo': 4, 'faur': 4, 'fuar': 4,
        'five': 5, 'fife': 5, 'fiv': 5, 'faiv': 5, 'phive': 5, 'fve': 5,
        'six': 6, 'sex': 6, 'sax': 6, 'siks': 6, 'sic': 6, 'sicks': 6, 'sx': 6,
        'seven': 7, 'saven': 7, 'svan': 7, 'sevan': 7, 'sevn': 7, 'svn': 7, 'sebhen': 7,
        'eight': 8, 'ate': 8, 'ait': 8, 'eit': 8, 'aight': 8, 'eght': 8, 'aat': 8,
        'nine': 9, 'nain': 9, 'nein': 9, 'nayn': 9, 'nin': 9, 'nyne': 9,
        'ten': 10, 'tan': 10, 'tun': 10, 'tenn': 10,
        'eleven': 11, 'twelv': 12, 'twelve': 12,
        'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
        'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
        'hundred': 100, 'sou': 100, 'sau': 100,

        # ── Hindi Numbers + ALL Spelling Variants ─────────────────────
        'ek': 1, 'ekk': 1, 'aek': 1, 'ak': 1, 'ikk': 1, 'eck': 1,
        'do': 2, 'doo': 2, 'doe': 2, 'doh': 2,
        'teen': 3, 'tin': 3, 'tiin': 3, 'theen': 3,
        'char': 4, 'chaar': 4, 'chhar': 4, 'chr': 4, 'cahr': 4,
        'paanch': 5, 'panch': 5, 'paach': 5, 'panc': 5, 'punch': 5, 'paanchh': 5, 'pach': 5,
        'chhe': 6, 'chay': 6, 'che': 6, 'chhay': 6, 'cheh': 6, 'chhai': 6, 'chai6': 6,
        'saat': 7, 'saath': 7, 'sat': 7, 'saaat': 7,
        'aath': 8, 'aat': 8, 'aaath': 8, 'aathh': 8, 'ath': 8,
        'nau': 9, 'now': 9, 'naw': 9, 'naoo': 9, 'nao': 9,
        'das': 10, 'dass': 10, 'daas': 10, 'duss': 10, 'dus': 10,
        'gyarah': 11, 'gyara': 11, 'giyarah': 11, 'gyaarah': 11,
        'barah': 12, 'bara': 12, 'baarah': 12, 'baara': 12,
        'terah': 13, 'tera': 13, 'tairah': 13,
        'chaudah': 14, 'chauda': 14, 'chodah': 14,
        'pandrah': 15, 'pandra': 15, 'pandara': 15,
        'solah': 16, 'sola': 16,
        'satrah': 17, 'satra': 17,
        'atharah': 18, 'athara': 18,
        'unnis': 19, 'unnees': 19,
        'bees': 20, 'bis': 20, 'biis': 20,
        'pacchees': 25, 'pacchis': 25, 'pachis': 25,
        'tees': 30, 'tiis': 30,
        'chaalees': 40, 'chalees': 40, 'chalis': 40,
        'pachaas': 50, 'pachas': 50, 'pachis50': 50,

        # ── Marathi Numbers ───────────────────────────────────────────
        'don': 2, 'tiin_m': 3,
        'chaar_m': 4,

        # ── Fractions & Multipliers (Hindi) ───────────────────────────
        'half': 0.5, 'quarter': 0.25,
        'adha': 0.5, 'aadha': 0.5, 'adhaa': 0.5, 'aadhi': 0.5, 'adhi': 0.5,
        'dhai': 2.5, 'dhaai': 2.5, 'dhi': 2.5, 'dhhai': 2.5, 'dai': 2.5,
        'dedh': 1.5, 'dedd': 1.5, 'dedha': 1.5, 'ded': 1.5,
        'paune': 0.75, 'pauna': 0.75, 'paunay': 0.75, 'pawne': 0.75,
        'pav': 0.25, 'paav': 0.25, 'paaw': 0.25,
        'sawa': 1.25, 'saawa': 1.25, 'sava': 1.25, 'savaa': 1.25,
        'double': 2, 'triple': 3, 'single': 1,
        'pair': 2, 'jodi': 2, 'jori': 2,
        'dozen': 12, 'darjan': 12, 'darzan': 12, 'darjen': 12,
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
    text = re.sub(r'\b(sold|sell|sale|selling|please|and|aur|the|a|an|some|of|also|give|add|more|i|want|need|get|me|us|becho|bech|de|dena|le|lo|lena|karo|nu|no|ko|ka|ki|ke|se|pe|p|par|on|for|to)\b', ' ', text, flags=re.IGNORECASE)
    
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

def consolidate_items(items_list):
    """Merge duplicate item names by summing their quantities (case-insensitive)."""
    merged = {}
    for item in items_list:
        name = item["item"]
        key = name.lower()
        if key in merged:
            merged[key]["qty"] += item["qty"]
        else:
            merged[key] = {"item": name, "qty": item["qty"]}
    return list(merged.values())

@app.post("/parse-order")
async def parse_order_endpoint(req: ChatRequest, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
    try:
        db, user_id = auth
        # FIX 6: Sanitize input
        message = req.message.strip()[:500]
        if not message:
            return {"success": False, "message": "Empty message"}
        if '<script' in message.lower() or '<iframe' in message.lower():
            raise HTTPException(status_code=400, detail="Invalid input detected")
        print(f"[PARSE] User {user_id} said: '{message}'")
        
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
        items_to_sell, payment_mode, customer_name = parse_message_locally(message, available_items, custom_aliases)
        
        # 2. AI Fallback if Local finds nothing
        if not items_to_sell:
            try:
                prompt = f"""
                You are a smart cashier. Parse this order: "{message}"
                Items available: {json.dumps(available_items)}
                
                CRITICAL RULES:
                - Return ONLY valid JSON, no explanation text
                - NEVER return duplicate item names. If same item appears multiple times, merge into ONE entry with combined quantity.
                - "3 curd" = [{{"item": "Curd", "qty": 3}}] NOT 3 separate entries
                - "curd curd curd" = [{{"item": "Curd", "qty": 3}}] NOT 3 entries with qty 1
                - Each item name must appear EXACTLY ONCE in the output array
                
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
        
        # 3. Consolidate duplicate items (merge by name, sum quantities)
        items_to_sell = consolidate_items(items_to_sell)
        
        # 4. Prepare response with pricing
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
    payment_mode: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)

@app.post("/confirm-order")
async def confirm_order_endpoint(req: ConfirmOrderRequest, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
    try:
        db, user_id = auth
        import uuid
        transaction_id = str(uuid.uuid4())[:8]
        
        # FIX 5: Input sanitization
        customer = re.sub(r'<[^>]+>', '', req.customer_name.strip())[:100]
        if not customer:
            customer = "Walk-in"
        if '<script' in customer.lower() or '<iframe' in customer.lower():
            raise HTTPException(status_code=400, detail="Invalid customer name")
        if req.payment_mode not in ("Cash", "Udhaar"):
            raise HTTPException(status_code=400, detail="Invalid payment mode. Must be Cash or Udhaar.")
        
        ITEM_PRICES, user_inventory = get_user_inventory_data(db, user_id)
        
        total_order_price = 0
        results = []
        failed = []
        
        for item_data in req.items:
            item = re.sub(r'<[^>]+>', '', item_data["item_name"].strip().title())[:100]
            qty = float(item_data["quantity"])
            if qty <= 0:
                failed.append(f"{item} (Invalid qty)")
                continue
        
            # Determine price (trust frontend price since user explicitly confirms it)
            # Fallback to backend price if missing
            fallback_unit_price = ITEM_PRICES.get(item, 0)
            unit_price = item_data.get("unit_price", fallback_unit_price)
            price = item_data.get("total_price", qty * unit_price)
            
            # If backend price was 0, learn the new price from this transaction
            if fallback_unit_price == 0 and unit_price > 0:
                try:
                    db.table("inventory").update({"price": unit_price}).eq("user_id", user_id).eq("item_name", item).execute()
                except Exception as e:
                    print(f"Price update error for {item}: {e}")
            
            # Check Stock (Aggregate)
            batches = db.table("inventory").select("id, stock_quantity, cost_price").eq("user_id", user_id).eq("item_name", item).order("expiry_date", desc=False).execute().data or []
            
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
                    # Deduct from this batch (FIX 7: floor at 0)
                    new_batch_stock = max(0, available - remaining_qty)
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
                "customer_name": customer,
                "user_id": user_id,
                "transaction_id": transaction_id,
                "payment_mode": req.payment_mode,
                "is_settled": req.payment_mode == 'Cash'
            }).execute()
            
            total_order_price += price
            results.append(f"{qty} {item}")
        
        # FIX 8: Update Dues if Udhaar (guard against Walk-in)
        if results and req.payment_mode == 'Udhaar' and customer.lower().strip() not in ('walk-in', 'walkin', 'walk in', ''):
            try:
                dues_check = db.table("dues").select("total_due").eq("user_id", user_id).eq("customer_name", customer).execute()
                if dues_check.data:
                    new_due = dues_check.data[0]['total_due'] + total_order_price
                    db.table("dues").update({"total_due": new_due, "last_updated": "now()"}).eq("user_id", user_id).eq("customer_name", customer).execute()
                else:
                    db.table("dues").insert({"customer_name": customer, "total_due": total_order_price, "user_id": user_id}).execute()
            except Exception as e:
                print(f"Dues Update Error: {e}")
                
        if failed:
            return {"success": len(results) > 0, "message": f"Saved {len(results)} items. Failed: {', '.join(failed)}", "failed_items": failed}
        
        return {"success": True, "message": f"✅ Order Confirmed: ₹{total_order_price}", "transaction_id": transaction_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Confirm order error: {e}", exc_info=True)
        return {"success": False, "message": f"❌ Order failed: {str(e)}"}


@app.get("/dues")
async def get_dues(request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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
async def get_customer_dues(customer_name: str, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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
                except Exception as e:
                    print(f"Date parse error: {e}")
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
async def get_todays_sales(request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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
        all_sales = response.data
        sales = [s for s in all_sales if s.get("item_name") != "Payment Received"]
        
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
                    except Exception as e:
                        print(f"Time parse error: {e}")
                
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
        model = genai.GenerativeModel('gemini-2.0-flash')
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
    item_name: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    price: Optional[float] = Field(default=None, ge=0)
    cost_price: Optional[float] = Field(default=None, ge=0)
    expiry_date: Optional[str] = None  # Format: YYYY-MM-DD

@app.post("/add-stock")
async def add_stock(req: AddStockRequest, background_tasks: BackgroundTasks, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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
             except Exception as e:
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
            
            # TRIGGER ALIAS GENERATION IF NEW
            if is_new_item:
                try:
                    insert_data["aliases"] = generate_multilingual_aliases(item)
                except Exception as alias_err:
                    print(f"Alias error (non-fatal): {alias_err}")
                
                # BACKGROUND Gemini enrichment — adds more creative aliases later
                background_tasks.add_task(generate_aliases_task, item, user_id, db)

            db.table("inventory").insert(insert_data).execute()

                
            return {"message": f"✅ Created {item} (Exp: {expiry or 'None'}) with stock: {qty}", "success": True}
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Add Stock Error: {e}")
        return {"message": f"❌ Error: {str(e)}", "success": False}

class ReduceStockRequest(BaseModel):
    item_name: str = Field(min_length=1)
    quantity: float = Field(gt=0)

@app.post("/reduce-stock")
async def reduce_stock(req: AddStockRequest, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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
    item_name: str = Field(min_length=1)

@app.delete("/delete-item")
async def delete_item(req: DeleteItemRequest, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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
    customer_name: str = Field(min_length=1)
    amount: Optional[float] = Field(default=None, gt=0)

@app.post("/dues/settle")
async def settle_dues(req: SettleDuesRequest, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
    try:
        db, user_id = auth
        customer = req.customer_name.strip()
        amount_to_settle = req.amount
        
        dues_check = db.table("dues").select("id, total_due").eq("user_id", user_id).eq("customer_name", customer).execute()
        if not dues_check.data or dues_check.data[0]["total_due"] <= 0:
            return {"message": f"No outstanding dues for {customer}", "success": False}
        
        total_due = dues_check.data[0]["total_due"]
        
        # Reject overpayment
        if amount_to_settle is not None and amount_to_settle > total_due:
            return {"message": f"❌ Amount ₹{round(amount_to_settle)} exceeds outstanding dues of ₹{round(total_due)}. Enter ₹{round(total_due)} or less.", "success": False}
        
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
async def get_weekly_analytics(request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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
async def get_monthly_sales(request: Request, month: int = None, year: int = None, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
    from datetime import datetime, timezone, timedelta
    try:
        db, user_id = auth
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        
        # Use provided month/year or default to current
        target_year = year or ist_now.year
        target_month = month or ist_now.month
        
        month_start = ist_now.replace(year=target_year, month=target_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        # Calculate end of month
        if target_month == 12:
            month_end = month_start.replace(year=target_year + 1, month=1)
        else:
            month_end = month_start.replace(month=target_month + 1)
        
        month_start_utc = month_start - timedelta(hours=5, minutes=30)
        month_end_utc = month_end - timedelta(hours=5, minutes=30)
        
        response = db.table("sales").select("*").eq("user_id", user_id).gte("created_at", month_start_utc.isoformat()).lt("created_at", month_end_utc.isoformat()).execute()
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
                except Exception as e:
                    print(f"Calendar date parse error: {e}")
        
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
                except Exception as e:
                    print(f"Calendar order count parse error: {e}")
        
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
                except Exception as e:
                    print(f"Calendar profit parse error: {e}")

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
            "month": month_start.strftime("%B %Y"),
            "year": target_year,
            "month_num": target_month,
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
async def get_date_sales(date: str, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
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

@app.get("/sales/year")
async def get_yearly_sales(request: Request, year: int = None, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
    from datetime import datetime, timezone, timedelta
    try:
        db, user_id = auth
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        if year is None:
            year = ist_now.year
        
        year_start = datetime(year, 1, 1, 0, 0, 0)
        year_end = datetime(year, 12, 31, 23, 59, 59)
        year_start_utc = year_start - timedelta(hours=5, minutes=30)
        year_end_utc = year_end - timedelta(hours=5, minutes=30)
        
        response = db.table("sales").select("*").eq("user_id", user_id).gte("created_at", year_start_utc.isoformat()).lte("created_at", year_end_utc.isoformat()).execute()
        all_sales = response.data
        sales = [s for s in all_sales if s.get("item_name") != "Payment Received"]
        
        # Group by month
        monthly_totals = {}
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        
        for s in sales:
            created = s.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    ist_dt = dt + timedelta(hours=5, minutes=30)
                    month_key = ist_dt.month  # 1-12
                    if month_key not in monthly_totals:
                        monthly_totals[month_key] = {"revenue": 0, "profit": 0, "orders": set(), "quantity": 0}
                    monthly_totals[month_key]["revenue"] += s.get("total_price", 0)
                    cost = s.get("total_cost", 0) or 0
                    monthly_totals[month_key]["profit"] += s.get("total_price", 0) - cost
                    monthly_totals[month_key]["quantity"] += s.get("quantity", 0)
                    tid = s.get("transaction_id") or created[:19]
                    monthly_totals[month_key]["orders"].add(tid)
                except Exception as e:
                    print(f"Yearly date parse error: {e}")
        
        months = []
        year_revenue = 0
        year_profit = 0
        for m in range(1, 13):
            data = monthly_totals.get(m)
            if data:
                rev = round(data["revenue"], 2)
                profit = round(data["profit"], 2)
                orders = len(data["orders"])
                qty = round(data["quantity"], 2)
                year_revenue += rev
                year_profit += profit
            else:
                rev = 0
                profit = 0
                orders = 0
                qty = 0
            months.append({
                "month": m,
                "name": month_names[m - 1],
                "revenue": rev,
                "profit": profit,
                "orders": orders,
                "quantity": qty
            })
        
        return {
            "year": year,
            "year_revenue": round(year_revenue, 2),
            "year_profit": round(year_profit, 2),
            "months": months
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Yearly Sales Error: {e}")
        return {"year": year or 2026, "year_revenue": 0, "year_profit": 0, "months": []}


# ============================================================================
# UDHAAR STATEMENT — Beautiful HTML Statement for WhatsApp/PDF Sharing
# ============================================================================

@app.get("/dues/{customer_name}/statement")
async def get_customer_statement(customer_name: str, request: Request, auth: tuple = Depends(get_user_client)):
    check_rate_limit(request)
    from datetime import datetime, timezone, timedelta
    import html as html_lib
    try:
        db, user_id = auth
        
        # Get Shop Name based on User ID
        shop_name = "My Store"
        try:
            profile = db.table("profiles").select("shop_name").eq("id", user_id).execute()
            if profile.data and profile.data[0].get("shop_name"):
                shop_name = profile.data[0]["shop_name"]
        except:
            if user_id == GUEST_USER_ID:
                shop_name = "AutoBill Buddy Demo Store"
        
        # FIX 15: Prevent HTML injection / XSS
        safe_customer = html_lib.escape(customer_name)
        safe_shop = html_lib.escape(shop_name)

        # Get total outstanding
        total_due = 0
        dues_res = db.table("dues").select("total_due").eq("user_id", user_id).eq("customer_name", customer_name).execute()
        if dues_res.data:
            total_due = dues_res.data[0]["total_due"]
            
        # Get transactions
        response = db.table("sales").select("item_name, quantity, total_price, created_at").eq(
            "user_id", user_id
        ).eq("customer_name", customer_name).eq("payment_mode", "Udhaar").order("created_at", desc=True).execute()
        
        # Build transaction rows
        rows_html = ""
        total_purchases = 0
        total_payments = 0
        for s in (response.data or []):
            date_str = ""
            created = s.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    ist = dt + timedelta(hours=5, minutes=30)
                    date_str = ist.strftime("%d %b %Y, %I:%M %p")
                except Exception:
                    date_str = created[:10]
            
            item_name = s.get("item_name", "")
            quantity = s.get("quantity", 0)
            total_price = s.get("total_price", 0)
            
            is_payment = item_name == "Payment Received"
            if is_payment:
                total_payments += abs(total_price)
            else:
                total_purchases += total_price
            
            price_class = "payment" if is_payment else "purchase"
            price_display = f"-₹{abs(total_price)}" if is_payment else f"₹{total_price}"
            
            rows_html += f"""
            <tr class="{price_class}">
                <td>{html_lib.escape(date_str)}</td>
                <td>{html_lib.escape(item_name)}</td>
                <td class="center">{quantity}</td>
                <td class="right">{price_display}</td>
            </tr>"""
        
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        statement_date = ist_now.strftime("%d %b %Y, %I:%M %p")
        
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Statement — {safe_customer}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            min-height: 100vh;
            padding: 24px;
            background: #0f172a;
            background-image: 
                radial-gradient(at 20% 20%, rgba(99, 102, 241, 0.15) 0%, transparent 50%),
                radial-gradient(at 80% 80%, rgba(244, 63, 94, 0.12) 0%, transparent 50%),
                radial-gradient(at 50% 50%, rgba(14, 165, 233, 0.08) 0%, transparent 60%);
        }}

        .statement {{
            max-width: 560px;
            margin: 0 auto;
            background: rgba(255,255,255, 0.97);
            border-radius: 24px;
            box-shadow: 
                0 0 0 1px rgba(255,255,255,0.1),
                0 20px 60px rgba(0,0,0,0.3),
                0 4px 16px rgba(0,0,0,0.15);
            overflow: hidden;
            backdrop-filter: blur(20px);
        }}

        /* ── HEADER ── */
        .header {{
            background: linear-gradient(135deg, #1e293b 0%, #334155 50%, #1e293b 100%);
            color: #fff;
            padding: 32px 32px 28px;
            position: relative;
            overflow: hidden;
        }}
        .header::before {{
            content: '';
            position: absolute;
            top: -50%;
            right: -30%;
            width: 200px;
            height: 200px;
            background: radial-gradient(circle, rgba(251,146,60,0.25), transparent 70%);
            border-radius: 50%;
        }}
        .header::after {{
            content: '';
            position: absolute;
            bottom: -40%;
            left: -20%;
            width: 160px;
            height: 160px;
            background: radial-gradient(circle, rgba(99,102,241,0.2), transparent 70%);
            border-radius: 50%;
        }}
        .shop-name {{
            font-size: 22px;
            font-weight: 900;
            letter-spacing: -0.5px;
            display: flex;
            align-items: center;
            gap: 10px;
            position: relative;
            z-index: 1;
        }}
        .shop-icon {{
            width: 38px;
            height: 38px;
            border-radius: 12px;
            background: linear-gradient(135deg, #f97316, #fb923c);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            box-shadow: 0 4px 12px rgba(249,115,22,0.4);
        }}
        .header-sub {{
            font-size: 11px;
            opacity: 0.6;
            font-weight: 500;
            margin-top: 8px;
            letter-spacing: 0.3px;
            position: relative;
            z-index: 1;
        }}
        .header-slogan {{
            font-size: 10px;
            opacity: 0.4;
            margin-top: 6px;
            font-style: italic;
            position: relative;
            z-index: 1;
        }}

        /* ── CUSTOMER BAR ── */
        .customer-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 24px 32px;
            background: linear-gradient(135deg, #fef2f2, #fff1f2);
            border-bottom: 1px solid #fecdd3;
        }}
        .customer-bar .name {{
            font-size: 17px;
            font-weight: 800;
            color: #1e293b;
            letter-spacing: -0.3px;
        }}
        .customer-bar .name-label {{
            font-size: 9px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #94a3b8;
            margin-bottom: 4px;
        }}
        .customer-bar .due {{
            text-align: right;
        }}
        .customer-bar .due-label {{
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #e11d48;
            margin-bottom: 2px;
        }}
        .customer-bar .due-amount {{
            font-size: 32px;
            font-weight: 900;
            color: #e11d48;
            letter-spacing: -1px;
            line-height: 1;
        }}
        .currency {{
            font-size: 20px;
            font-weight: 700;
            opacity: 0.7;
            vertical-align: top;
            margin-right: 1px;
        }}

        /* ── SUMMARY CARDS ── */
        .summary {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
            padding: 24px 32px;
            border-bottom: 1px solid #f1f5f9;
        }}
        .summary-card {{
            padding: 16px;
            border-radius: 16px;
            text-align: center;
            position: relative;
            overflow: hidden;
        }}
        .summary-card::before {{
            content: '';
            position: absolute;
            top: -20px;
            right: -20px;
            width: 60px;
            height: 60px;
            border-radius: 50%;
            opacity: 0.1;
        }}
        .summary-card.purchases {{
            background: linear-gradient(145deg, #fff7ed, #ffedd5);
            border: 1px solid #fed7aa;
        }}
        .summary-card.purchases::before {{ background: #ea580c; }}
        .summary-card.payments {{
            background: linear-gradient(145deg, #f0fdf4, #dcfce7);
            border: 1px solid #bbf7d0;
        }}
        .summary-card.payments::before {{ background: #16a34a; }}
        .summary-card .card-icon {{
            font-size: 20px;
            margin-bottom: 6px;
        }}
        .summary-card .label {{
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 6px;
        }}
        .summary-card.purchases .label {{ color: #c2410c; }}
        .summary-card.payments .label {{ color: #15803d; }}
        .summary-card .value {{
            font-size: 22px;
            font-weight: 900;
            letter-spacing: -0.5px;
        }}
        .summary-card.purchases .value {{ color: #ea580c; }}
        .summary-card.payments .value {{ color: #16a34a; }}

        /* ── TABLE ── */
        .table-wrapper {{
            padding: 0 16px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        thead th {{
            padding: 14px 16px;
            text-align: left;
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.2px;
            color: #94a3b8;
            border-bottom: 2px solid #e2e8f0;
        }}
        thead th.center {{ text-align: center; }}
        thead th.right {{ text-align: right; }}
        tbody tr {{
            transition: background 0.2s ease;
        }}
        tbody tr:hover {{
            background: #f8fafc;
        }}
        tbody td {{
            padding: 14px 16px;
            border-bottom: 1px solid #f1f5f9;
            color: #475569;
            font-weight: 500;
        }}
        tbody td:first-child {{
            font-size: 11px;
            color: #94a3b8;
            font-weight: 600;
            white-space: nowrap;
        }}
        tbody td.center {{ text-align: center; }}
        tbody td.right {{ text-align: right; font-weight: 700; }}
        tr.purchase td.right {{ color: #dc2626; }}
        tr.payment td {{ color: #059669; }}
        tr.payment td.right {{ color: #059669; font-weight: 800; }}
        tr.payment {{
            background: linear-gradient(90deg, #f0fdf4, #ecfdf5);
        }}
        tr.payment:hover {{
            background: linear-gradient(90deg, #dcfce7, #d1fae5);
        }}
        tr.payment td:nth-child(2)::before {{
            content: '✓ ';
            font-weight: 800;
        }}

        /* ── FOOTER ── */
        .footer {{
            padding: 24px 32px;
            text-align: center;
            background: #f8fafc;
            border-top: 1px solid #e2e8f0;
        }}
        .footer .date {{
            font-size: 10px;
            color: #94a3b8;
            font-weight: 600;
            letter-spacing: 0.3px;
        }}
        .footer .brand {{
            font-size: 10px;
            color: #cbd5e1;
            margin-top: 6px;
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
        }}
        .footer .powered {{
            font-size: 9px;
            color: #e2e8f0;
            margin-top: 4px;
        }}

        /* ── BUTTONS ── */
        .no-print {{
            text-align: center;
            margin: 24px auto;
            max-width: 560px;
            display: flex;
            gap: 12px;
            justify-content: center;
        }}
        .no-print button {{
            padding: 14px 28px;
            border: none;
            border-radius: 14px;
            font-size: 13px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.4,0,0.2,1);
            letter-spacing: 0.2px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .btn-pdf {{
            background: linear-gradient(135deg, #1e293b, #334155);
            color: #fff;
            box-shadow: 0 4px 16px rgba(30,41,59,0.3);
        }}
        .btn-pdf:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(30,41,59,0.4);
        }}
        .btn-whatsapp {{
            background: linear-gradient(135deg, #25d366, #128C7E);
            color: #fff;
            box-shadow: 0 4px 16px rgba(37,211,102,0.3);
            flex: 1;
            justify-content: center;
        }}
        .btn-whatsapp:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(37,211,102,0.4);
        }}
        .btn-whatsapp:disabled {{
            opacity: 0.7;
            transform: none;
            cursor: wait;
        }}
        .btn-wa-text {{
            background: linear-gradient(135deg, #128C7E, #075E54);
            color: #fff;
            box-shadow: 0 4px 16px rgba(18,140,126,0.3);
            flex: 1;
            justify-content: center;
        }}
        .btn-wa-text:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(18,140,126,0.4);
        }}

        @media print {{
            body {{ padding: 0; background: #fff; }}
            .statement {{ box-shadow: none; border-radius: 0; }}
            .no-print {{ display: none !important; }}
            .header {{ background: #1e293b !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            tr.payment {{ background: #f0fdf4 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
        }}

        @media (max-width: 480px) {{
            body {{ padding: 12px; }}
            .header {{ padding: 24px 20px 20px; }}
            .customer-bar {{ padding: 20px; }}
            .summary {{ padding: 20px; gap: 10px; }}
            .no-print {{ flex-direction: column; padding: 0 12px; }}
            .no-print button {{ justify-content: center; }}
        }}
    </style>
</head>
<body>
    <div class="statement">
        <div class="header">
            <div class="shop-name">
                <div class="shop-icon">🏪</div>
                {safe_shop}
            </div>
            <div class="header-sub">📋 Udhaar Statement — Transaction History & Outstanding Balance</div>
            <div class="header-slogan">"Aaj nagad, kal udhaar" — par hisaab toh rakhna padega! 😄</div>
        </div>
        
        <div class="customer-bar">
            <div>
                <div class="name-label">Customer</div>
                <div class="name">{safe_customer}</div>
            </div>
            <div class="due">
                <div class="due-label">Balance Due</div>
                <div class="due-amount"><span class="currency">₹</span>{round(total_due)}</div>
            </div>
        </div>
        
        <div class="summary">
            <div class="summary-card purchases">
                <div class="card-icon">🛒</div>
                <div class="label">Total Purchases</div>
                <div class="value">₹{round(total_purchases)}</div>
            </div>
            <div class="summary-card payments">
                <div class="card-icon">💸</div>
                <div class="label">Total Payments</div>
                <div class="value">₹{round(total_payments)}</div>
            </div>
        </div>
        
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Date / Time</th>
                        <th>Item</th>
                        <th class="center">Qty</th>
                        <th class="right">Amount</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html if rows_html else '<tr><td colspan="4" style="text-align:center;padding:32px;color:#94a3b8;font-weight:600;">No transactions found</td></tr>'}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <div class="date">Statement generated on {statement_date}</div>
            <div class="brand">AutoBill Buddy</div>
            <div class="powered">Powered by AI</div>
        </div>
    </div>
    
    <div class="no-print">
        <button class="btn-pdf" onclick="savePDF()">📄 Save PDF</button>
        <button class="btn-whatsapp" onclick="sendAsPdf()">
            <span>📎 Send as PDF</span>
        </button>
        <button class="btn-wa-text" onclick="sendAsText()">
            <span>💬 Send as Text</span>
        </button>
    </div>
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
    <script>
        var PHONE = ''; // Phone number is not passed to the backend for security reasons
        var CUSTOMER = '{safe_customer}';
        var TOTAL_DUE = {total_due};
        var SHOP = '{safe_shop}';
        var TOTAL_PURCHASES = {total_purchases};
        var TOTAL_PAYMENTS = {total_payments};
        
        function getPdfOptions() {{
            return {{
                margin: [10, 0, 10, 0],
                filename: 'Statement_' + CUSTOMER.replace(/\\s+/g, '_') + '.pdf',
                image: {{ type: 'jpeg', quality: 0.98 }},
                html2canvas: {{ scale: 2, useCORS: true, logging: false }},
                jsPDF: {{ unit: 'mm', format: 'a4', orientation: 'portrait' }}
            }};
        }}
        
        function savePDF() {{
            var el = document.querySelector('.statement');
            html2pdf().set(getPdfOptions()).from(el).save();
        }}
        
        function buildWaUrl(msg) {{
            var encoded = encodeURIComponent(msg);
            if (PHONE) {{
                return 'https://wa.me/' + PHONE + '?text=' + encoded;
            }}
            return 'https://wa.me/?text=' + encoded;
        }}
        
        function sendAsPdf() {{
            var el = document.querySelector('.statement');
            var nl = String.fromCharCode(10);
            var waMsg = '*Udhaar Statement*' + nl
                + '*' + SHOP + '*' + nl + nl
                + 'Customer: *' + CUSTOMER + '*' + nl
                + 'Balance Due: *Rs.' + TOTAL_DUE + '*' + nl + nl
                + '_\"Aaj nagad, kal udhaar\" - par hisaab toh rakhna padega!_';
            
            // Try native share with file (works on mobile)
            if (navigator.canShare) {{
                html2pdf().set(getPdfOptions()).from(el).outputPdf('blob').then(function(blob) {{
                    var fileName = 'Statement_' + CUSTOMER.replace(/\\s+/g, '_') + '.pdf';
                    var file = new File([blob], fileName, {{ type: 'application/pdf' }});
                    
                    if (navigator.canShare({{ files: [file] }})) {{
                        navigator.share({{
                            text: waMsg,
                            files: [file]
                        }}).catch(function(err) {{
                            console.log('Share cancelled or failed:', err);
                            // Fallback: save PDF + open WhatsApp
                            html2pdf().set(getPdfOptions()).from(el).save();
                            window.open(buildWaUrl(waMsg), '_blank');
                        }});
                    }} else {{
                        // canShare exists but can't share files — fallback
                        html2pdf().set(getPdfOptions()).from(el).save();
                        window.open(buildWaUrl(waMsg + nl + nl + 'PDF saved on device, please attach it.'), '_blank');
                    }}
                }});
            }} else {{
                // Desktop fallback: open WhatsApp first, then save PDF
                window.open(buildWaUrl(waMsg + nl + nl + 'PDF bill saved on device, please attach and send it.'), '_blank');
                html2pdf().set(getPdfOptions()).from(el).save();
            }}
        }}
        
        function sendAsText() {{
            var nl = String.fromCharCode(10);
            var lines = [];
            lines.push('*UDHAAR STATEMENT*');
            lines.push('*' + SHOP + '*');
            lines.push('');
            lines.push('Customer: *' + CUSTOMER + '*');
            lines.push('Balance Due: *Rs.' + TOTAL_DUE + '*');
            lines.push('');
            lines.push('Total Purchases: Rs.' + TOTAL_PURCHASES);
            lines.push('Total Payments: Rs.' + TOTAL_PAYMENTS);
            lines.push('');
            lines.push('--- Items ---');
            
            // Collect transaction rows from the table
            var rows = document.querySelectorAll('tbody tr');
            rows.forEach(function(row) {{
                var cells = row.querySelectorAll('td');
                if (cells.length >= 4) {{
                    var date = cells[0].textContent.trim();
                    var item = cells[1].textContent.trim();
                    var qty = cells[2].textContent.trim();
                    var amt = cells[3].textContent.trim();
                    lines.push(date + ' | ' + item + ' x' + qty + ' = ' + amt);
                }}
            }});
            
            lines.push('');
            lines.push('_\"Aaj nagad, kal udhaar\" - par hisaab toh rakhna padega!_');
            
            var msg = lines.join(nl);
            window.open(buildWaUrl(msg), '_blank');
        }}
    </script>
</body>
</html>"""
        
        return HTMLResponse(content=html)
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HTMLResponse(content=f"<h1>Error generating statement</h1><p>{str(e)}</p>", status_code=500)


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