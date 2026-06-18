import json
import requests

from config import TG_TOKEN


def send_message(chat_id, text, button_text, url):
    button = {
        "text": button_text,
        "url": url,
        "style": "success"
    }
    reply_markup = {
        "inline_keyboard": [[button]]
    }
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)


    response = requests.post(url, json=payload, timeout=1)
    return response.json()
