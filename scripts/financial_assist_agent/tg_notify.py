import os
import requests

TG_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

def send(message: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("⚠️  TG_BOT_TOKEN 或 TG_CHAT_ID 未設定")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    })