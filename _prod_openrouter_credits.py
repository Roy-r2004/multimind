import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

key = None
for line in Path("c:/multi-mind-verdict/.env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"\s*OPENROUTER_API_KEY\s*=\s*(.+)\s*$", line)
    if m:
        key = m.group(1).strip().strip('"').strip("'")
        break

if not key:
    raise SystemExit("no OPENROUTER_API_KEY found")

print("key tail:", key[-6:])

for path in ["/api/v1/credits", "/api/v1/auth/key"]:
    req = urllib.request.Request(
        "https://openrouter.ai" + path,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(path, "->", resp.status, resp.read().decode()[:600])
    except urllib.error.HTTPError as e:
        print(path, "-> HTTP", e.code, e.read().decode(errors="replace")[:400])
    except Exception as e:  # noqa: BLE001
        print(path, "-> ERROR", type(e).__name__, e)
