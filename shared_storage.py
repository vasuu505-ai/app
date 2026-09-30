# shared_storage.py
"""
Centralized OTP storage, app detection, country normalization,
and real-time notification engine (SSE + FCM) for all panels.
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

def detect_app_and_sender(sender: str, message: str):
    """Detect app_id, clean sender name, and icon_slug"""
    sender_lower = (sender or "").lower()
    msg_lower = (message or "").lower()
    combined = f"{sender_lower} {msg_lower}"

    for app in KNOWN_APPS:
        for kw in app["keywords"]:
            if kw in sender_lower or kw in msg_lower:
                return app["id"], app["name"], app["icon_slug"]

    # If sender looks like a human-readable service name (not just random numbers)
    clean_sender = sender.strip() if sender else "SMS"
    if clean_sender.isdigit() or len(clean_sender) > 25:
        clean_sender = "SMS"

    slug = re.sub(r"[^a-z0-9]+", "_", clean_sender.lower()).strip("_")
    if not slug or slug.isdigit():
        slug = "other"

    return slug, clean_sender, slug

def extract_clean_otp(existing_otp, message: str) -> str:
    """Return clean OTP code without clutter"""
    if existing_otp and str(existing_otp).strip():
        val = str(existing_otp).strip()
        # Keep dashed format like 484-073 or pure digits
        if re.match(r"^\d{3}[-\s]\d{3}$", val) or re.match(r"^\d{4,8}$", val):
            return val

    text = (message or "").replace("\n", " ").strip()
    # 1. 123-456 or 123 456
    m = re.search(r"\b(\d{3})[-\s](\d{3})\b", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # 2. Keyed OTP (e.g., "code is 123456")
    m = re.search(r"(?:otp|code|pin|password|verification|код|кода)[^\d]{0,10}(\d{4,8})", text, re.I)
    if m:
        return m.group(1)

    m = re.search(r"(\d{4,8})[^\w]{0,10}(?:otp|code|pin|password|verification|код|коda)", text, re.I)
    if m:
        return m.group(1)

    # 3. Any 4-8 digit number that isn't a calendar year
    for g in re.findall(r"\b\d{4,8}\b", text):
        if not (1900 <= int(g) <= 2099):
            return g

    return ""


class SharedOTPStorage:
    def __init__(self):
        self.otp_messages = []
        self.seen = set()
        self.lock = threading.Lock()
        self.MAX_OTP_STORAGE = 500000

        # Real-time SSE subscriber queues
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

    def add_otp(self, otp_data: dict) -> bool:
        """Add OTP from any panel (thread-safe) with cleaning & event broadcasting"""
        otp_id = otp_data.get('id')
        if not otp_id:
            return False

        with self.lock:
            # Check duplicate
            if otp_id in self.seen:
                return False

            # Normalization and enrichment
            raw_message = otp_data.get('message', '')
            raw_sender = otp_data.get('sender', '')
            app_id, clean_sender, icon_slug = detect_app_and_sender(raw_sender, raw_message)
            clean_otp = extract_clean_otp(otp_data.get('otp'), raw_message)
            country = normalize_country(otp_data.get('country', 'Unknown'))
            clean_num = clean_phone_number(otp_data.get('number', ''))

            # Enrich the dictionary
            otp_data['app_id'] = app_id
            otp_data['sender'] = clean_sender
            otp_data['icon_slug'] = icon_slug
            otp_data['clean_otp'] = clean_otp
            otp_data['country'] = country
            otp_data['clean_number'] = clean_num
            if not otp_data.get('otp'):
                otp_data['otp'] = clean_otp
            if not otp_data.get('timestamp'):
                otp_data['timestamp'] = datetime.now(timezone.utc).isoformat()

            self.seen.add(otp_id)
            self.otp_messages.insert(0, otp_data)

            # Limit storage
            if len(self.otp_messages) > self.MAX_OTP_STORAGE:
                removed = self.otp_messages.pop()
                self.seen.discard(removed.get('id'))

        # Broadcast to SSE clients asynchronously
        self._broadcast_sse({
            "event": "new_otp",
            "data": otp_data
        })

        # Send FCM Push Notification
        self._dispatch_fcm_for_otp(otp_data)

        return True

    def get_all(self):
        """Get all OTPs"""
        with self.lock:
            return self.otp_messages.copy()

    def get_for_number(self, target_number: str):
        """Get messages for a specific number (clean digit comparison or suffix match)"""
        clean_target = clean_phone_number(target_number)
        if not clean_target:
            return []

        with self.lock:
            matched = []
            for msg in self.otp_messages:
                num = msg.get('clean_number') or clean_phone_number(msg.get('number', ''))
                if num == clean_target or num.endswith(clean_target) or clean_target.endswith(num):
                    matched.append(msg)
            return matched

    def count_for_number(self, target_number: str) -> int:
        return len(self.get_for_number(target_number))

    def clear(self):
        """Clear all OTPs"""
        with self.lock:
            self.otp_messages.clear()
            self.seen.clear()

    def count(self):
        """Get OTP count"""
        with self.lock:
            return len(self.otp_messages)

    # ===== SSE REAL-TIME PUBSUB =====
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

    def _broadcast_sse(self, event_dict: dict):
        otp_data = event_dict.get("data", {})
        clean_num = otp_data.get("clean_number") or clean_phone_number(otp_data.get("number", ""))

        with self.sse_lock:
            for sub in list(self.sse_subscribers):
                filter_num = sub.get("filter_number")
                if filter_num and filter_num not in clean_num and clean_num not in filter_num:
                    continue
                try:
                    sub["queue"].put_nowait(event_dict)
                except queue.Full:
                    # Drop slow consumer
                    try:
                        self.sse_subscribers.remove(sub)
                    except ValueError:
                        pass

    def broadcast_custom_notification(self, title: str, body: str, target_number: str = None, app_id: str = "general"):
        """Broadcast manual push notification from admin panel"""
        payload = {
            "title": title,
            "body": body,
            "number": target_number or "all",
            "app_id": app_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        # SSE broadcast
        with self.sse_lock:
            for sub in list(self.sse_subscribers):
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

    def _dispatch_fcm_for_otp(self, otp_data: dict):
        clean_num = otp_data.get("clean_number") or clean_phone_number(otp_data.get("number", ""))
        sender = otp_data.get("sender", "SMS")
        clean_otp = otp_data.get("clean_otp") or otp_data.get("otp", "")
        body_text = f"New OTP for {otp_data.get('number')}: {clean_otp} ({sender})" if clean_otp else otp_data.get('message', '')

        title = f"{sender} Code Received"
        payload = {
            "id": otp_data.get("id"),
            "number": otp_data.get("number"),
            "sender": sender,
            "app_id": otp_data.get("app_id"),
            "clean_otp": clean_otp,
            "otp": clean_otp
        }

        self._dispatch_fcm_notification(title, body_text, payload, clean_num)

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

        # Send via FCM HTTP v1 / legacy protocol in background thread
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

# Global instance
shared_storage = SharedOTPStorage()
