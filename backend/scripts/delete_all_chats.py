"""Delete all chats for the demo org via API."""

import asyncio

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://localhost:8001", timeout=60) as client:
        signin = await client.post(
            "/api/v1/auth/signin",
            json={"email": "chafic@gmail.com", "password": "password123"},
        )
        if signin.status_code != 200:
            signin = await client.post(
                "/api/v1/auth/signin",
                json={"email": "admin@gmail.com", "password": "password123"},
            )
        signin.raise_for_status()
        token = signin.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = (await client.get("/api/v1/auth/session", headers=headers)).json()
        headers["X-Org-Id"] = session["organization"]["id"]

        chats = (await client.get("/api/v1/chats", headers=headers)).json()
        print(f"found {len(chats)} chats")
        for chat in chats:
            resp = await client.delete(f"/api/v1/chats/{chat['id']}", headers=headers)
            print(f"  {chat['id'][:8]}… {resp.status_code}")

        remaining = (await client.get("/api/v1/chats", headers=headers)).json()
        print(f"remaining {len(remaining)}")


if __name__ == "__main__":
    asyncio.run(main())
