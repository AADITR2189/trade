"""Checks the Telegram setup: which bot the token belongs to, whether it can see your chat,
and sends a test message. Prints Telegram's answers (never the token)."""
import os

import requests

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
print(f"Token set: {bool(token)} (length {len(token)}), chat ID set: {bool(chat_id)} (ends with ...{chat_id[-3:]})")
api = f"https://api.telegram.org/bot{token}"

me = requests.get(f"{api}/getMe", timeout=20).json()
print("Bot:", me.get("result", {}).get("username") if me.get("ok") else me)

chat = requests.get(f"{api}/getChat", params={"chat_id": chat_id}, timeout=20).json()
if chat.get("ok"):
    c = chat["result"]
    print(f"Chat: type={c.get('type')} first_name={c.get('first_name')} username={c.get('username')}")
else:
    print("Chat lookup failed:", chat.get("description"))

hook = requests.get(f"{api}/getWebhookInfo", timeout=20).json()
print("Webhook:", hook.get("result", {}).get("url") or "none")

r = requests.post(f"{api}/sendMessage", timeout=20, data={
    "chat_id": chat_id, "text": "🧪 Test from your trade helper: if you can read this, Telegram works."}).json()
if r.get("ok"):
    m = r["result"]
    print(f"Sent OK: message_id={m['message_id']} to chat type={m['chat']['type']} "
          f"first_name={m['chat'].get('first_name')} via bot {m['from']['username']}")
else:
    print("Send FAILED:", r.get("error_code"), r.get("description"))
