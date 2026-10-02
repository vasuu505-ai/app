# shared_storage.py
"""
Centralized Notification Engine (FCM + SSE), app detection,
and country normalization for FreeNumber backend.
All external panel scrapers and SMS scraping logic removed.
"""

import threading
import queue
import logging
import re
import os
import json
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ===== KNOWN APPS CATALOG =====
KNOWN_APPS = [
    {"id": "whatsapp",  "name": "WhatsApp",    "icon_slug": "whatsapp",  "keywords": ["whatsapp", "wa.me", "whatapp"]},
    {"id": "telegram",  "name": "Telegram",    "icon_slug": "telegram",  "keywords": ["telegram", "tg", "telegram.org"]},
    {"id": "google",    "name": "Google",      "icon_slug": "google",    "keywords": ["google", "g-", "gmail", "youtube"]},
    {"id": "uber",      "name": "Uber",        "icon_slug": "uber",      "keywords": ["uber", "uber code"]},
    {"id": "facebook",  "name": "Facebook",    "icon_slug": "facebook",  "keywords": ["facebook", "fb", "meta"]},
    {"id": "instagram", "name": "Instagram",   "icon_slug": "instagram", "keywords": ["instagram", "ig"]},
    {"id": "discord",   "name": "Discord",     "icon_slug": "discord",   "keywords": ["discord"]},
    {"id": "twitter",   "name": "Twitter / X", "icon_slug": "twitter",   "keywords": ["twitter", "x.com"]},
    {"id": "tiktok",    "name": "TikTok",      "icon_slug": "tiktok",    "keywords": ["tiktok", "bytedance"]},
    {"id": "amazon",    "name": "Amazon",      "icon_slug": "amazon",    "keywords": ["amazon", "amzn"]},
    {"id": "paypal",    "name": "PayPal",      "icon_slug": "paypal",    "keywords": ["paypal"]},
    {"id": "microsoft", "name": "Microsoft",   "icon_slug": "microsoft", "keywords": ["microsoft", "msft", "outlook", "azure"]},
    {"id": "apple",     "name": "Apple",       "icon_slug": "apple",     "keywords": ["apple", "icloud"]},
    {"id": "snapchat",  "name": "Snapchat",    "icon_slug": "snapchat",  "keywords": ["snapchat", "snap"]},
    {"id": "netflix",   "name": "Netflix",     "icon_slug": "netflix",   "keywords": ["netflix"]},
    {"id": "viber",     "name": "Viber",       "icon_slug": "viber",     "keywords": ["viber"]},
    {"id": "line",      "name": "LINE",        "icon_slug": "line",      "keywords": ["line app", "line:"]},
    {"id": "tinder",    "name": "Tinder",      "icon_slug": "tinder",    "keywords": ["tinder"]},
    {"id": "binance",   "name": "Binance",     "icon_slug": "binance",   "keywords": ["binance"]}
]

COUNTRY_NORMALIZATION = {
    "russian federation": "Russia",
    "united states of america": "United States",
    "united states": "United States",
    "united kingdom of great britain and northern ireland": "United Kingdom",
    "united kingdom": "United Kingdom",
    "czechia": "Czech Republic",
    "iran, islamic republic of": "Iran",
    "korea, republic of": "South Korea",
    "viet nam": "Vietnam",
    "india": "India",
}

def normalize_country(country_name: str) -> str:
    if not country_name:
        return "Unknown"
    cleaned = str(country_name).strip()
    return COUNTRY_NORMALIZATION.get(cleaned.lower(), cleaned)

def clean_phone_number(raw_num: str) -> str:
    """Strip all non-digit characters for robust matching"""
    if not raw_num:
        return ""
    return re.sub(r"\D", "", str(raw_num))


class SharedNotificationStorage:
    def __init__(self):
        # Real-time SSE subscriber queues for notifications
        self.sse_subscribers = []
        self.sse_lock = threading.Lock()

        # Push notification token subscriptions: { phone_number: [fcm_tokens...] }
        self.fcm_subscriptions = {}
        self.fcm_lock = threading.Lock()
        self.SUBSCRIPTIONS_FILE = "subscriptions.json"
        self._load_subscriptions()

    def _load_subscriptions(self):
        try:
            if os.path.exists(self.SUBSCRIPTIONS_FILE):
                with open(self.SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
                    self.fcm_subscriptions = json.load(f)
        except Exception as e:
            logger.error(f"Error loading subscriptions: {e}")
            self.fcm_subscriptions = {}

    def _save_subscriptions(self):
        try:
            with open(self.SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.fcm_subscriptions, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving subscriptions: {e}")

    # ===== SSE REAL-TIME PUBSUB FOR NOTIFICATIONS =====
    def subscribe_sse(self, filter_number: str = None) -> queue.Queue:
        q = queue.Queue(maxsize=100)
        clean_filter = clean_phone_number(filter_number) if filter_number else None
        item = {"queue": q, "filter_number": clean_filter}
        with self.sse_lock:
            self.sse_subscribers.append(item)
        return q

    def unsubscribe_sse(self, q: queue.Queue):
        with self.sse_lock:
            self.sse_subscribers = [sub for sub in self.sse_subscribers if sub["queue"] is not q]

    def broadcast_custom_notification(self, title: str, body: str, target_number: str = None, app_id: str = "general"):
        """Broadcast manual push notification from admin panel to SSE and FCM"""
        clean_num = clean_phone_number(target_number) if target_number else None
        payload = {
            "title": title,
            "body": body,
            "number": target_number or "all",
            "clean_number": clean_num or "all",
            "app_id": app_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # SSE broadcast
        with self.sse_lock:
            for sub in list(self.sse_subscribers):
                filter_num = sub.get("filter_number")
                if filter_num and clean_num and (filter_num not in clean_num and clean_num not in filter_num):
                    continue
                try:
                    sub["queue"].put_nowait({"event": "notification", "data": payload})
                except queue.Full:
                    pass

        # FCM dispatch
        self._dispatch_fcm_notification(title, body, payload, target_number)

    # ===== FCM PUSH NOTIFICATIONS =====
    def register_fcm_token(self, token: str, number: str = None):
        """Subscribe a device token to a phone number or global updates"""
        clean_num = clean_phone_number(number) if number else "__global__"
        with self.fcm_lock:
            if clean_num not in self.fcm_subscriptions:
                self.fcm_subscriptions[clean_num] = []
            if token not in self.fcm_subscriptions[clean_num]:
                self.fcm_subscriptions[clean_num].append(token)
                self._save_subscriptions()
        logger.info(f"Registered FCM token for number: {clean_num}")
        return True

    def _dispatch_fcm_notification(self, title: str, body: str, payload_data: dict, target_number: str = None):
        """Send FCM notification using FCM_SERVER_KEY if available"""
        fcm_server_key = os.getenv("FCM_SERVER_KEY")

        # Collect target tokens
        tokens = set()
        with self.fcm_lock:
            # Global tokens
            tokens.update(self.fcm_subscriptions.get("__global__", []))
            # Number-specific tokens
            if target_number:
                clean_target = clean_phone_number(target_number)
                for num_key, tok_list in self.fcm_subscriptions.items():
                    if num_key == clean_target or num_key.endswith(clean_target) or clean_target.endswith(num_key):
                        tokens.update(tok_list)

        if not tokens:
            return

        if not fcm_server_key:
            logger.info(f"FCM_SERVER_KEY not configured. Would have sent notification '{title}' to {len(tokens)} token(s).")
            return

        # Send via FCM HTTP in background thread
        threading.Thread(
            target=self._send_fcm_http,
            args=(list(tokens), title, body, payload_data, fcm_server_key),
            daemon=True
        ).start()

    def _send_fcm_http(self, tokens: list, title: str, body: str, data: dict, server_key: str):
        url = "https://fcm.googleapis.com/fcm/send"
        headers = {
            "Authorization": f"key={server_key}",
            "Content-Type": "application/json"
        }
        for token in tokens:
            body_json = {
                "to": token,
                "notification": {
                    "title": title,
                    "body": body,
                    "sound": "default"
                },
                "data": {str(k): str(v) for k, v in data.items()},
                "priority": "high"
            }
            try:
                res = requests.post(url, headers=headers, json=body_json, timeout=10)
                logger.info(f"FCM sent to {token[:10]}... status={res.status_code}")
            except Exception as e:
                logger.error(f"FCM send failed: {e}")

    # Compatibility methods
    def get_subscriber_count(self) -> int:
        with self.fcm_lock:
            unique_tokens = set()
            for token_list in self.fcm_subscriptions.values():
                unique_tokens.update(token_list)
            return len(unique_tokens)

    def count(self) -> int:
        return 0

    def count_for_number(self, target_number: str) -> int:
        return 0

    def get_for_number(self, target_number: str) -> list:
        return []

    def get_all(self) -> list:
        return []

    def clear(self):
        pass


# Global instance
shared_storage = SharedNotificationStorage()
