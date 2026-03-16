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

# Indian market cost prices (per unit) - realistic wholesale/purchase prices
# These are approximate wholesale rates a kirana store would pay
MARKET_COST_PRICES = {
    "Milk": 25,         # SP: ~30-56, CP: ~25/litre (Aananda/Amul bulk)
    "Bread": 30,        # SP: 40, CP: ~30
    "Rice": 38,         # SP: 50/kg, CP: ~38/kg
    "Sugar": 36,        # SP: 45/kg, CP: ~36/kg (not for 20 SP version)
    "Oil": 125,         # SP: 150/litre, CP: ~125
    "Eggs": 5.5,        # SP: 7/piece, CP: ~5.5
    "Butter": 45,       # SP: 55, CP: ~45 (Amul 100g)
    "Curd": 30,         # SP: 35-45, CP: ~30
    "Paneer": 60,       # SP: 80/pack, CP: ~60
    "Salt": 18,         # SP: 25, CP: ~18
    "Wheat": 28,        # SP: 35/kg, CP: ~28
    "Maida": 30,        # SP: 40/kg, CP: ~30
    "Suji": 38,         # SP: 50/kg, CP: ~38
    "Poha": 35,         # SP: 45/kg, CP: ~35
    "Flour": 26,        # SP: 32-35, CP: ~26
    "Atta": 32,         # SP: 40/kg, CP: ~32
    "Toor Dal": 110,    # SP: 140/kg, CP: ~110
    "Chana Dal": 70,    # SP: 90/kg, CP: ~70
    "Urad Dal": 95,     # SP: 120/kg, CP: ~95
    "Moong Dal": 100,   # SP: 130/kg, CP: ~100
    "Dal": 90,          # SP: 120/kg, CP: ~90
    "Daal": 18,         # SP: 23, CP: ~18
    "Rajma": 115,       # SP: 150/kg, CP: ~115
    "Chana": 60,        # SP: 80/kg, CP: ~60
    "Tea": 200,         # SP: 250/pack, CP: ~200
    "Coffee": 320,      # SP: 400/pack, CP: ~320
    "Ghee": 450,        # SP: 550/kg, CP: ~450
    "Mustard Oil": 140, # SP: 180/litre, CP: ~140
    "Soap": 28,         # SP: 40, CP: ~28
    "Detergent": 90,    # SP: 120, CP: ~90
    "Toothpaste": 75,   # SP: 100 (Colgate), CP: ~75
    "Biscuits": 22,     # SP: 30, CP: ~22
    "Noodles": 11,      # SP: 15, CP: ~11 (Maggi single)
    "Chips": 7,         # SP: 10, CP: ~7
    "Red Chilli": 220,  # SP: 300/kg, CP: ~220
    "Coriander": 110,   # SP: 150/kg, CP: ~110
    "Turmeric": 150,    # SP: 200/kg, CP: ~150
    "Cumin": 280,       # SP: 350/kg, CP: ~280
    "Cheese": 14,       # SP: 18 (slice), CP: ~14
    "Bottle": 15,       # SP: 20-30 (water), CP: ~15
    "Saridon": 7,       # SP: 10 (strip), CP: ~7
    "Murgi": 170,       # SP: 220/kg, CP: ~170
    "Dhosa": 30,        # SP: 40 (mix), CP: ~30
    "Coca Cola": 35,    # SP: 45, CP: ~35
    "Bag": 350,         # SP: 500, CP: ~350
    "Phone": 8000,      # SP: 10000, CP: ~8000
    "Clip": 12,         # Already has CP, skip
    "Vaseline": 19,     # Already has CP, skip
}

# Fetch all items without cost_price
res = client.table("inventory").select("id, item_name, price, cost_price").execute()
items = res.data or []

updated = 0
skipped = 0

for item in items:
    # Skip items that already have a cost_price > 0
    if item.get("cost_price") and item["cost_price"] > 0:
        continue
    
    name = item["item_name"]
    sell_price = item.get("price", 0) or 0
    
    # Look up market cost price
    if name in MARKET_COST_PRICES:
        cp = MARKET_COST_PRICES[name]
    elif sell_price > 0:
        # Fallback: set CP as ~75% of sell price (typical kirana margin)
        cp = round(sell_price * 0.75, 2)
    else:
        # No sell price either, skip
        skipped += 1
        print(f"  ⏭ Skipped {name} (ID: {item['id']}) - no sell price")
        continue
    
    # Ensure CP doesn't exceed sell price
    if sell_price > 0 and cp >= sell_price:
        cp = round(sell_price * 0.75, 2)
    
    # Update in database
    try:
        client.table("inventory").update({"cost_price": cp}).eq("id", item["id"]).execute()
        margin = round(((sell_price - cp) / sell_price) * 100, 1) if sell_price > 0 else 0
        print(f"  ✅ {name} | SP: ₹{sell_price} → CP: ₹{cp} | Margin: {margin}%")
        updated += 1
    except Exception as e:
        print(f"  ❌ Failed {name}: {e}")
        skipped += 1

print(f"\n{'='*50}")
print(f"✅ Updated: {updated}")
print(f"⏭ Skipped: {skipped}")
