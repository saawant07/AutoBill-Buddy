import re

text = "3 anda 5kg chawal anuj"

EXPANDED_VOICE_TYPOS = {
    'keji': 'kg', 'kaji': 'kg', 'kilo': 'kg', 'kilos': 'kg', 'kilogram': 'kg',
    'doodh': 'milk', 'dudh': 'milk', 'dudth': 'milk', 'melk': 'milk', 'malk': 'milk', 'milkk': 'milk',
    'chawal': 'rice', 'chaawal': 'rice', 'chaval': 'rice', 'rise': 'rice', 'rais': 'rice', 'raice': 'rice', 'ricee': 'rice',
    'cheeni': 'sugar', 'chini': 'sugar', 'shakkar': 'sugar', 'suger': 'sugar', 'sugur': 'sugar', 'sugarr': 'sugar',
    'aloo': 'potato', 'alu': 'potato', 'aaloo': 'potato',
    'pyaz': 'onion', 'pyaaz': 'onion', 'kanda': 'onion',
    'tamatar': 'tomato', 'tamater': 'tomato',
    'anda': 'eggs', 'ande': 'eggs', 'anday': 'eggs', 'ags': 'eggs', 'aggs': 'eggs', 'eggz': 'eggs', 'eg': 'eggs',
}

available_items = ["Rice", "Milk"]
custom_aliases = {}

credit_kws = 'udhaar|udhar|udhhaar|credit|khata|khate|khatte|uthaar|uthar|oodhar|oodaar|udhaari|udhari|naam|name'
hindi_markers = 'ko|ka|ki|ke|se|kaa|kee|kai|kha|kh'

customer_patterns = [
    rf'(?:on|for)\s+([A-Za-z]+?)\s+(?:{credit_kws})',
    rf'([A-Za-z]+?)\s+(?:{hindi_markers})\s+(?:{credit_kws})',
    rf'([A-Za-z]+?)\s+(?:{credit_kws})',
    r'(?:to|for)\s+([A-Za-z]+?)\s+on',
    r'(?:ke\s+naam\s+se|naam)\s+([A-Za-z]+?)\s+(?:on\s+)?(?:' + credit_kws + ')',
    r'([A-Za-z]+?)\s+(?:ke|kha)\s+(?:' + credit_kws + r')\s+(?:pe|par|on)',
    r'(?:on|for)\s+([A-Za-z]+?)\s+(?:on|for)\s+([A-Za-z]+?)\s+(?:' + credit_kws + ')',
    
    # Standalone fallbacks (Trailing or Leading)
    r'\b([A-Za-z]{2,})\s*$',
    r'^\s*([A-Za-z]{2,})\b'
]

original_text = text
detected_customer = 'Walk-in'
non_names = {'on', 'the', 'and', 'sold', 'sell', 'sale', 'give', 'some', 'also', 'more', 'cash', 'udhaar', 'milk', 'bread', 'sugar', 'rice', 'oil', 'eggs', 'butter', 'cheese', 'paneer', 'curd', 'atta', 'dal', 'tea', 'coffee', 'ghee', 'soap', 'chips', 'noodles', 'biscuits', 'toothpaste', 'detergent', 'flour', 'salt', 'wheat', 'maida', 'suji', 'poha', 'jeera', 'cumin', 'khatte', 'khata', 'ke', 'ka', 'ki', 'ko', 'se', 'pe', 'naam', 'name', 'kg', 'kgs', 'kilo', 'kilos', 'liter', 'liters', 'ltr', 'ml', 'gram', 'grams', 'gm', 'gms', 'g', 'piece', 'pieces', 'pcs', 'packet', 'packets', 'pack'}

for item in available_items:
    for w in item.lower().split():
        non_names.add(w)
for typo in EXPANDED_VOICE_TYPOS.keys():
    non_names.add(typo.lower())
for cat_alias in custom_aliases.keys():
    for w in cat_alias.lower().split():
        non_names.add(w)

for pat in customer_patterns:
    m = re.search(pat, original_text, re.IGNORECASE)
    if m:
        group_idx = 2 if m.lastindex == 2 else 1
        candidate = m.group(group_idx).strip().title()
        if candidate.lower() not in non_names and len(candidate) >= 2 and not candidate.replace('.','').isdigit():
            detected_customer = candidate
            print(f"Matched pattern: {pat}")
            break

print(f"Customer: {detected_customer}")

text2 = "anuj 3 anda 5kg chawal"
for pat in customer_patterns:
    m = re.search(pat, text2, re.IGNORECASE)
    if m:
        group_idx = 2 if m.lastindex == 2 else 1
        candidate = m.group(group_idx).strip().title()
        if candidate.lower() not in non_names and len(candidate) >= 2 and not candidate.replace('.','').isdigit():
            detected_customer = candidate
            print(f"Matched pattern: {pat}")
            break

print(f"Customer: {detected_customer}")

