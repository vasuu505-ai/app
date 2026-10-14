"""
panel_cdr.py — CDR Panel (158.51.78.74)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API token based (X-API-Token header) — no login required
URL: http://158.51.78.74/api/messages/cdr
"""

import requests
import re
import time
import hashlib
import logging
import os
import phonenumbers
import pycountry
from collections import deque

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ⚙️ CONFIG
# ─────────────────────────────────────────────
PANEL_NAME = "cdr_panel"

# Token ko env variable se set karo:  export CDR_API_TOKEN="your-token"
API_TOKEN = os.getenv("CDR_API_TOKEN", "CN-17UTE7LL")
API_URL = os.getenv("CDR_API_URL", "http://158.51.78.74/api/messages/cdr")
POLL_INTERVAL = 6.0

# Kaun se status wale messages lene hain
ALLOWED_STATUS = {"received", "pending", "cleared"}

_HEADERS = {
    "X-API-Token": API_TOKEN,
    "Accept": "application/json",
}

_enabled = True


def is_enabled() -> bool:
    return _enabled


def set_enabled(state: bool):
    global _enabled
    _enabled = state
    logger.info(f"[{PANEL_NAME}] Panel enabled state set to: {state}")


def _country_from_number(num: str) -> str:
    try:
        parsed = phonenumbers.parse("+" + num)
        region = phonenumbers.region_code_for_number(parsed)
        return pycountry.countries.get(alpha_2=region).name
    except Exception:
        return "Unknown"


def _extract_otp(message: str) -> str | None:
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


def _wait(stop_event, seconds: float) -> bool:
    """Sleep karta hai. True return kare to loop exit karna hai."""
    if stop_event:
        return stop_event.wait(seconds)
    time.sleep(seconds)
    return False


def start(otp_queue, seen_messages: set, seen_order: deque, max_seen: int, stop_event=None):
    logger.info(f"[{PANEL_NAME}] 🚀 Starting panel (X-API-Token mode)...")

    if not API_TOKEN:
        logger.error(f"[{PANEL_NAME}] ❌ CDR_API_TOKEN set nahi hai. Env variable set karo.")

    while True:
        # Check stop signal
        if stop_event and stop_event.is_set():
            logger.info(f"[{PANEL_NAME}] 🛑 Stop event received — exiting loop.")
            break

        if not is_enabled():
            if _wait(stop_event, POLL_INTERVAL):
                break
            continue

        try:
            response = requests.get(API_URL, headers=_HEADERS, timeout=10)

            if response.status_code == 429:
                retry_after = 30
                try:
                    retry_after = int(response.headers.get("Retry-After", 30))
                except Exception:
                    pass
                logger.warning(f"[{PANEL_NAME}] ⚠️ Rate limited, retry after {retry_after}s")
                if _wait(stop_event, retry_after):
                    break
                continue

            if response.status_code in (401, 403):
                logger.error(f"[{PANEL_NAME}] ❌ Invalid/expired token (HTTP {response.status_code}). Check CDR_API_TOKEN.")
                if _wait(stop_event, 30):
                    break
                continue

            if response.status_code != 200:
                logger.warning(f"[{PANEL_NAME}] ⚠️ Unexpected HTTP status {response.status_code}: {response.text[:120]}")
                if _wait(stop_event, 5):
                    break
                continue

            try:
                data = response.json()
            except Exception:
                logger.warning(f"[{PANEL_NAME}] ⚠️ Non-JSON response: {response.text[:100]}")
                if _wait(stop_event, 5):
                    break
                continue

            # API seedha list return karti hai (safety ke liye dict bhi handle)
            if isinstance(data, dict):
                rows = data.get("records") or data.get("data") or []
            else:
                rows = data

            # Purane se naye order me process karo taaki queue me order sahi rahe
            try:
                rows = sorted(rows, key=lambda r: str(r.get("time", "")))
            except Exception:
                pass

            for row in rows:
                if not isinstance(row, dict):
                    continue

                status = str(row.get("status", "")).strip().lower()
                if status and status not in ALLOWED_STATUS:
                    continue

                raw_num = str(row.get("number", "")).strip()
                number = re.sub(r"[^\d]", "", raw_num)
                if number.startswith("00"):
                    number = number[2:]
                sender = str(row.get("cllr") or row.get("cli") or "").strip() or "Unknown"
                message = str(row.get("content", "")).strip()
                dt = str(row.get("time", "")).strip()

                if not number or not message:
                    continue

                # Dedupe: id ho to wahi use karo, warna time+number+message
                msg_id = str(row.get("id", "")).strip()
                if msg_id:
                    hash_id = hashlib.md5(msg_id.encode()).hexdigest()
                else:
                    hash_id = hashlib.md5(f"{dt}{number}{message[:50]}".encode()).hexdigest()

                if hash_id in seen_messages:
                    continue

                seen_messages.add(hash_id)
                seen_order.append(hash_id)
                if len(seen_order) > max_seen:
                    old = seen_order.popleft()
                    seen_messages.discard(old)

                country = _country_from_number(number)
                if country == "Unknown" and row.get("range"):
                    range_text = str(row.get("range", "")).strip()
                    for c in pycountry.countries:
                        if c.name.lower() in range_text.lower():
                            country = c.name
                            break

                otp = _extract_otp(message)

                otp_queue.put({
                    "hash_id": hash_id,
                    "panel_name": PANEL_NAME,
                    "dt": dt,
                    "country": country,
                    "num": number,
                    "cli": sender,
                    "message": message,
                    "otp": otp
                })
                logger.info(f"[{PANEL_NAME}] 📱 {number} | {sender} | OTP: {otp or 'N/A'}")

        except requests.exceptions.Timeout:
            logger.warning(f"[{PANEL_NAME}] ⚠️ Timeout — retrying...")
        except requests.exceptions.ConnectionError:
            logger.warning(f"[{PANEL_NAME}] ⚠️ Connection error — retrying...")
        except Exception as e:
            logger.error(f"[{PANEL_NAME}] ❌ Fetch loop error: {e}")

        # Sleep interval with responsive stop_event interrupt
        if _wait(stop_event, POLL_INTERVAL):
            logger.info(f"[{PANEL_NAME}] 🛑 Stop event received during wait — exiting loop.")
            break
