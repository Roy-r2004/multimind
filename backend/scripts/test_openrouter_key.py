"""Quick smoke test for OpenRouter key + backend turn creation."""

import asyncio
import sys

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://localhost:8001", timeout=120) as client:
        signin = await client.post(
            "/api/v1/auth/signin",
            json={"email": "chafic@acme.co", "password": "password123"},
        )
        signin.raise_for_status()
        token = signin.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session = (await client.get("/api/v1/auth/session", headers=headers)).json()
        org_id = session["organization"]["id"]
        headers["X-Org-Id"] = org_id

        models = (await client.get("/api/v1/models", headers=headers)).json()
        print(f"models: {len(models)}")

        chat = (await client.post("/api/v1/chats", headers=headers, json={"title": "Key test"})).json()
        turn_resp = await client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            headers=headers,
            json={"user_message": "Say hello in two words.", "model_set_id": "balanced"},
        )
        print(f"turn_status: {turn_resp.status_code}")
        if turn_resp.is_success:
            turn = turn_resp.json()
            print(f"turn_id: {turn['id']} status: {turn['status']} answers: {len(turn.get('model_answers', []))}")
        else:
            print(turn_resp.text[:500])
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
