# maitt_scraper.py — working otp_scraper_thread logic + shared_storage integration
"""
Maitt Panel - (API token based, no login required)
Uses the proven working scraper logic from the telegram bot.
"""

import requests
import re
import time
import hashlib
import logging
import os
import unicodedata
import phonenumbers
import pycountry
from datetime import datetime
from collections import deque
from shared_storage import shared_storage

logger = logging.getLogger(__name__)

PANEL_ID   = "maitt"
PANEL_NAME = "Maitt Panel"

# ===== CONFIG =====
API_TOKEN = os.getenv("MAITT_API_TOKEN", "R09SRzRSQkFIYFNhU4xyVlqKiWR1f4V2dG25VYl9ti0NCh3FkYW51")
BASE_URL  = os.getenv("MAITT_BASE_URL", "http://51.77.216.195/crapi/mait")

# Persistent seen file — restart ke baad bhi duplicates nahi aayenge
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maitt_seen.txt")

seen_messages = set()
seen_order    = deque(maxlen=200000)

# ===== PERSISTENT SEEN — same as working bot =====

def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                for line in f:
                    uid = line.strip()
                    if uid:
                        seen_messages.add(uid)
                        seen_order.append(uid)
            logger.info(f"[{PANEL_ID}] 📂 Loaded {len(seen_messages)} seen IDs from disk")
        except Exception as e:
            logger.warning(f"[{PANEL_ID}] Could not load seen file: {e}")

def is_message_seen(msg_id: str) -> bool:
    return msg_id in seen_messages

def mark_seen(msg_id: str):
    seen_messages.add(msg_id)
    seen_order.append(msg_id)
    try:
        with open(SEEN_FILE, "a") as f:
            f.write(msg_id + "\n")
    except Exception as e:
        logger.warning(f"[{PANEL_ID}] Could not save seen ID: {e}")

# ===== HELPERS =====

def mask_number(number: str) -> str:
    return number[:2] + "••" + number[-4:] if len(number) > 6 else number

def country_from_number(num: str):
    try:
        parsed = phonenumbers.parse("+" + num)
        region = phonenumbers.region_code_for_number(parsed)
        country = pycountry.countries.get(alpha_2=region).name
        flag = "".join(chr(127397 + ord(c)) for c in region)
        return country, flag
    except Exception:
        return "Unknown", "🌍"

def extract_otp(message: str):
    """Bilkul bot2.py wala exact logic"""
    text = message.replace("\n", " ").strip()

    m = re.search(r"\b(\d{3})[-\s](\d{3})\b", text)
    if m:
        return m.group(1) + m.group(2)

    m = re.search(r"(otp|code|pin|password|verification|код|кода)[^\d]{0,10}(\d{4,8})", text, re.I)
    if m:
        return m.group(2)

    m = re.search(r"(\d{4,8})[^\w]{0,10}(otp|code|pin|password|verification|код|кода)", text, re.I)
    if m:
        return m.group(1)

    for g in re.findall(r"\b\d{4,8}\b", text):
        if not (1900 <= int(g) <= 2099):
            return g

    return None


# ===== OTP LOOP — exact working logic from telegram bot =====

def fetch_otp_loop():
    logger.info(f"[{PANEL_ID}] 🔄 OTP loop starting...")

    while True:
        try:
            response = requests.get(
                f"{BASE_URL}/viewstats",
                params={
                    "token":   API_TOKEN,
                    "dt1":     "2026-02-25 00:00:00",
                    "dt2":     "2099-12-31 23:59:59",
                    "records": 10
                },
                timeout=8
            )

            # Empty response check
            if not response.text.strip():
                logger.warning(f"[{PANEL_ID}] ⚠️ API returned empty response — token invalid ya server down")
                time.sleep(5)
                continue

            try:
                stats = response.json()
            except Exception:
                logger.warning(f"[{PANEL_ID}] ⚠️ API non-JSON response: {response.text[:100]}")
                time.sleep(5)
                continue

            if stats.get("status") == "error":
                logger.warning(f"[{PANEL_ID}] ⚠️ API error: {stats.get('msg', 'Unknown')}")
                time.sleep(10)
                continue

            if stats.get("status") == "success":
                for record in stats["data"]:
                    # Working bot ka exact msg_id format
                    msg_id = f"{record.get('dt')}_{record.get('num')}_{record.get('message', '')[:50]}"

                    if is_message_seen(msg_id):
                        continue

                    mark_seen(msg_id)

                    num     = str(record.get("num", "")).strip()
                    sender  = str(record.get("cli") or "Unknown").strip()
                    message = str(record.get("message") or "").strip()
                    time_   = str(record.get("dt") or "").strip()
                    otp     = extract_otp(message)

                    country, flag = country_from_number(num)

                    otp_data = {
                        'id':            msg_id,
                        'time':          time_,
                        'country':       country,
                        'flag':          flag,
                        'number':        num,
                        'masked_number': mask_number(num),
                        'sender':        sender,
                        'message':       message,
                        'otp':           otp,
                        'timestamp':     datetime.now().isoformat(),
                        'panel':         PANEL_NAME
                    }

                    if shared_storage.add_otp(otp_data):
                        logger.info(f"[{PANEL_ID}] 📨 {num} | {otp or 'N/A'} | {sender}")

            time.sleep(3)

        except requests.exceptions.Timeout:
            logger.warning(f"[{PANEL_ID}] ⚠️ API timeout — retrying...")
            time.sleep(3)
        except requests.exceptions.ConnectionError:
            logger.warning(f"[{PANEL_ID}] ⚠️ API connection error — retrying...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"[{PANEL_ID}] ❌ Scraper error: {e}")
            time.sleep(2)

# ===== ENTRY POINT =====

def start_scraper():
    logger.info(f"[{PANEL_ID}] 🚀 Starting {PANEL_NAME}...")
    load_seen()
    fetch_otp_loop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    start_scraper()
