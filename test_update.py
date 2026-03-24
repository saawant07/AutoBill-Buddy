import asyncio
from httpx import AsyncClient

async def run():
    async with AsyncClient() as client:
        res = await client.post("http://localhost:8000/update-item", 
            json={"item_name":"Milk", "price":32.5, "cost_price":28},
            headers={"Authorization": "Bearer GUEST_MODE_NO_AUTH"}
        )
        print(res.status_code)
        print(res.json())

asyncio.run(run())
