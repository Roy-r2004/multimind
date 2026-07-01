import asyncio

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://localhost:8001", timeout=120) as c:
        r = await c.post("/api/v1/auth/signin", json={"email": "chafic@gmail.com", "password": "password123"})
        r.raise_for_status()
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        chat = (await c.post("/api/v1/chats", json={"title": "T"}, headers=h)).json()
        tr = await c.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"user_message": "Hi", "model_set_id": "balanced"},
            headers=h,
        )
        print(tr.status_code)
        print(tr.text)


if __name__ == "__main__":
    asyncio.run(main())
