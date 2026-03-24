import requests

token = "GUEST_MODE_NO_AUTH"

res = requests.post(
    "http://127.0.0.1:8000/add-stock",
    json={
        "item_name": "Test Item 123",
        "quantity": 10,
        "price": 50,
        "cost_price": 40
    },
    headers={"Authorization": f"Bearer {token}"}
)

print(res.status_code)
print(res.text)
