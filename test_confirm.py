import asyncio
from httpx import AsyncClient

async def run():
    async with AsyncClient() as client:
        res = await client.post("http://localhost:8000/confirm-order", 
            json={"items":[{"item_name":"bread", "quantity":1, "total_price":10}], "payment_mode":"Cash", "customer_name":"Walk-in Customer"},
            headers={"Authorization": "Bearer GUEST_MODE_NO_AUTH"}
        )
        print(res.status_code)
        print(res.json())

asyncio.run(run())
