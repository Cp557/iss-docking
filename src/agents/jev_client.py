"""Small HTTP client for TypeSafe Jev decisions."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


API_URL = "https://api.typesafe.ai/v1/systemone"
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_api_key() -> str:
    key = os.environ.get("JEV_API_KEY")
    if key:
        return key

    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "JEV_API_KEY":
                key = value.strip().strip('"\'')
                if key:
                    return key

    raise ValueError("JEV_API_KEY is missing from the environment and .env")


def ask_jev(state: dict, questions: dict) -> dict:
    payload = {"model": "jev-latest", "state": state, "questions": questions}
    request = Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {load_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.load(response)
