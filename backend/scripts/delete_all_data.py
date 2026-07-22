"""Delete all chats and lessons for the signed-in org via API."""

import asyncio
import os


import httpx

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8001")
EMAIL = os.environ.get("SEED_EMAIL", "chafic@gmail.com")
PASSWORD = os.environ.get("SEED_PASSWORD", "password123")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120) as client:
        signin = await client.post(
            "/api/v1/auth/signin",
            json={"email": EMAIL, "password": PASSWORD},
        )
        if signin.status_code != 200:
            signin = await client.post(
                "/api/v1/auth/signin",
                json={"email": "admin@gmail.com", "password": PASSWORD},
            )
        signin.raise_for_status()
        token = signin.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session = (await client.get("/api/v1/auth/session", headers=headers)).json()
        headers["X-Org-Id"] = session["organization"]["id"]

        lessons = (await client.get("/api/v1/lessons", headers=headers)).json()
        print(f"found {len(lessons)} lessons")
        for lesson in lessons:
            resp = await client.delete(f"/api/v1/lessons/{lesson['id']}", headers=headers)
            print(f"  lesson {lesson['id'][:8]}… {resp.status_code}")

        chats = (await client.get("/api/v1/chats", headers=headers)).json()
        print(f"found {len(chats)} chats")
        for chat in chats:
            resp = await client.delete(f"/api/v1/chats/{chat['id']}", headers=headers)
            print(f"  chat {chat['id'][:8]}… {resp.status_code}")

        remaining_lessons = (await client.get("/api/v1/lessons", headers=headers)).json()
        remaining_chats = (await client.get("/api/v1/chats", headers=headers)).json()
        print(f"remaining lessons {len(remaining_lessons)}, chats {len(remaining_chats)}")


if __name__ == "__main__":
    asyncio.run(main())
