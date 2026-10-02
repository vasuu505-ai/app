# main.py — FreeNumber Numbers & Push Notification Server
"""
Production-ready API & Management Backend:
- Secure Admin Passcode Authentication
- Embedded Web Admin Dashboard at /admin
- Numbers Management (Single & Bulk Add, Edit, Delete, Filter)
- Supported Apps Matrix: GET /api/apps
- Real-Time Push Notifications (FCM / SSE):
    - Subscribe: POST /api/notifications/subscribe
    - Admin Broadcast: POST /api/admin/notifications/send
    - Real-Time SSE Stream: GET /api/notifications/stream

Environment Variables:
  ADMIN_PASSWORD   — Admin passcode (default: YEAR2030#)
  FCM_SERVER_KEY   — Firebase Server Key (optional for phone push notifications)
  PORT             — Server port (default: 8080)
"""

from flask import Flask, jsonify, request, Response, render_template, stream_with_context
from flask_cors import CORS
import threading
import os
import logging
import json
import time
import hashlib
import queue
from shared_storage import shared_storage, KNOWN_APPS, clean_phone_number, normalize_country

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# ===== CONFIG =====
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "YEAR2030#")
PORT           = int(os.getenv("PORT", 8080))
NUMBERS_FILE   = "numbers_data.json"

# ===== FLASK APP =====
app = Flask(__name__, template_folder="templates")

# CORS — mobile apps & external frontends can call from any origin
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ===== NUMBERS STORAGE & MIGRATION =====
numbers_data = []
numbers_lock = threading.Lock()

DEFAULT_SUPPORTED_APPS = [
    "whatsapp", "telegram", "google", "uber", "facebook", "instagram", "discord"
]

def normalize_number_entry(entry: dict) -> dict:
    """Ensure every number has full schema with supported_apps, status, etc."""
    raw_num = str(entry.get('number', '')).strip()
    c_code = str(entry.get('countryCode', '')).strip()
    if c_code and not c_code.startswith('+') and c_code.isdigit():
        c_code = '+' + c_code

    return {
        'id': entry.get('id') or hashlib.md5((c_code + raw_num + str(time.time())).encode()).hexdigest(),
        'number': raw_num,
        'countryCode': c_code or '+1',
        'country': normalize_country(entry.get('country', 'Unknown')),
        'flag': entry.get('flag') or '🌍',
        'status': entry.get('status', 'active'),
        'supported_apps': entry.get('supported_apps') if entry.get('supported_apps') is not None else list(DEFAULT_SUPPORTED_APPS),
        'blocked_apps': entry.get('blocked_apps') if entry.get('blocked_apps') is not None else [],
        'received_sms_count': int(entry.get('received_sms_count', 0))
    }

def load_numbers():
    global numbers_data
    with numbers_lock:
        try:
            if os.path.exists(NUMBERS_FILE):
                with open(NUMBERS_FILE, 'r', encoding='utf-8') as f:
                    raw_list = json.load(f)
                numbers_data = [normalize_number_entry(n) for n in raw_list]
                logger.info(f"Loaded {len(numbers_data)} numbers from {NUMBERS_FILE}")
            else:
                numbers_data = []
                logger.warning(f"{NUMBERS_FILE} not found — initialized empty list")
        except Exception as e:
            logger.error(f"Load error: {e}")
            numbers_data = []

def save_numbers():
    with numbers_lock:
        try:
            with open(NUMBERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(numbers_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Save error: {e}")

# ===== ADMIN AUTH SECURITY =====
def check_admin():
    """Verify admin passcode from Header, Bearer token, query param, or JSON body"""
    # 1. Header: X-Admin-Key
    header_key = request.headers.get('X-Admin-Key')
    if header_key and header_key == ADMIN_PASSWORD:
        return True

    # 2. Header: Authorization: Bearer <password>
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header.split(' ', 1)[1].strip()
        if token == ADMIN_PASSWORD:
            return True

    # 3. Query param: ?key=... or ?admin_key=...
    q_key = request.args.get('admin_key') or request.args.get('key')
    if q_key and q_key == ADMIN_PASSWORD:
        return True

    # 4. JSON body: {"password": "..."}
    body = request.get_json(silent=True) or {}
    if body.get('password') == ADMIN_PASSWORD or body.get('admin_key') == ADMIN_PASSWORD:
        return True

    return False

# ===== ROOT, HEALTH & ADMIN WEB UI =====

@app.route('/')
def root():
    return jsonify({
        "status": "running",
        "service": "FreeNumber API",
        "admin_ui": "/admin",
        "endpoints": {
            "numbers": "/api/numbers",
            "numbers_stats": "/api/numbers/stats",
            "apps": "/api/apps",
            "notifications_subscribe": "/api/notifications/subscribe",
            "notifications_stream": "/api/notifications/stream",
            "admin_verify": "/api/admin/verify",
            "admin_numbers": "/api/admin/numbers",
            "admin_notifications_send": "/api/admin/notifications/send",
            "health": "/health"
        }
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "numbers": len(numbers_data),
        "push_subscribers": shared_storage.get_subscriber_count(),
        "sse_subscribers": len(shared_storage.sse_subscribers)
    }), 200

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/admin')
def admin_page():
    """Web Admin Dashboard for numbers management & push notifications"""
    return render_template('admin.html')

# ===== PUBLIC APIS =====

@app.route('/api/numbers')
def get_numbers():
    """
    Returns list of numbers with supported_apps, blocked_apps, country, flag, and status.
    Filters: ?country=..., ?search=..., ?app=...
    """
    country = request.args.get('country', 'all')
    search  = request.args.get('search', '').lower()
    app_id  = request.args.get('app', '').lower()

    with numbers_lock:
        data = list(numbers_data)

    enriched = [dict(n) for n in data]

    if country != 'all':
        enriched = [n for n in enriched if n['countryCode'] == country or n['country'].lower() == country.lower()]
    if app_id:
        enriched = [n for n in enriched if app_id in [a.lower() for a in n.get('supported_apps', [])] and app_id not in [b.lower() for b in n.get('blocked_apps', [])]]
    if search:
        enriched = [n for n in enriched
                    if search in n['number'].lower()
                    or search in n['country'].lower()
                    or search in n['countryCode'].lower()]

    return jsonify({
        'success': True,
        'numbers': enriched,
        'total': len(enriched)
    })

@app.route('/api/numbers/stats')
def get_numbers_stats():
    with numbers_lock:
        data = list(numbers_data)

    countries = {}
    for n in data:
        c = n['countryCode']
        if c not in countries:
            countries[c] = {'name': n['country'], 'flag': n['flag'], 'count': 0}
        countries[c]['count'] += 1

    return jsonify({
        'success': True,
        'total_numbers': len(data),
        'total_countries': len(countries),
        'countries': countries
    })

@app.route('/api/apps')
def get_apps():
    """
    Catalog of supported apps and services with active count.
    """
    with numbers_lock:
        active_numbers = [n for n in numbers_data if n.get('status', 'active') == 'active']

    apps_list = []
    for app_info in KNOWN_APPS:
        aid = app_info["id"]
        matching_countries = set()
        for n in active_numbers:
            sup = [s.lower() for s in n.get('supported_apps', [])]
            blk = [b.lower() for b in n.get('blocked_apps', [])]
            if aid in sup and aid not in blk:
                matching_countries.add(n.get('country', 'Unknown'))

        count = len(matching_countries)
        apps_list.append({
            "id": aid,
            "name": app_info["name"],
            "icon_slug": app_info["icon_slug"],
            "available_countries_count": count,
            "status": "available" if count > 0 else "unavailable"
        })

    return jsonify(apps_list)

# Compatibility stub for client apps that query messages or otps
@app.route('/api/numbers/<path:number>/messages')
def get_number_messages(number):
    return jsonify({
        "number": number,
        "country": "Unknown",
        "total_messages": 0,
        "messages": []
    })

@app.route('/api/otps')
def get_otps():
    return jsonify({'success': True, 'otps': [], 'total': 0})

# ===== REAL-TIME SSE STREAM & PUSH NOTIFICATIONS =====

@app.route('/api/notifications/stream')
@app.route('/api/otps/stream')
def stream_notifications():
    """
    Server-Sent Events (SSE) Live Stream for push alerts.
    Supports filtering by specific number: ?number=...
    """
    filter_number = request.args.get('number')
    q = shared_storage.subscribe_sse(filter_number)

    def event_stream():
        try:
            yield ": connected\n\n"
            loops = 0
            # Keep stream open for up to 60 * 5s = 300 seconds (5 minutes)
            # Reconnecting seamlessly guarantees zero dead/zombie threads
            while loops < 60:
                loops += 1
                try:
                    event_dict = q.get(timeout=5)
                    event_type = event_dict.get("event", "message")
                    data_str = json.dumps(event_dict.get("data", {}))
                    if event_type == "message":
                        yield f"data: {data_str}\n\n"
                    else:
                        yield f"event: {event_type}\ndata: {data_str}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        except (GeneratorExit, Exception):
            pass
        finally:
            shared_storage.unsubscribe_sse(q)

    response = Response(stream_with_context(event_stream()), mimetype="text/event-stream")
    response.headers['Cache-Control'] = 'no-cache, no-transform'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response

@app.route('/api/notifications/subscribe', methods=['POST'])
def subscribe_notifications():
    """
    Subscribe Firebase Cloud Messaging (FCM) token
    Request: {"fcm_token": "...", "number": "..."}
    """
    body = request.get_json(silent=True) or {}
    token = body.get('fcm_token')
    number = body.get('number')

    if not token:
        return jsonify({'success': False, 'message': 'Missing fcm_token'}), 400

    shared_storage.register_fcm_token(token, number)
    return jsonify({
        'success': True,
        'message': f'Subscribed successfully to notifications for {number or "all numbers"}'
    })

# ===== ADMIN APIS =====

@app.route('/api/admin/verify', methods=['POST'])
def verify_admin():
    if check_admin():
        return jsonify({'success': True, 'message': 'Authenticated'})
    return jsonify({'success': False, 'message': 'Invalid admin password'}), 401

@app.route('/api/admin/numbers', methods=['POST'])
def add_numbers():
    if not check_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    body         = request.get_json(silent=True) or {}
    country      = body.get('country')
    country_code = body.get('countryCode')
    flag         = body.get('flag')
    nums         = body.get('numbers', [])
    status       = body.get('status', 'active')
    supported    = body.get('supported_apps') or list(DEFAULT_SUPPORTED_APPS)
    blocked      = body.get('blocked_apps') or []

    if not all([country, country_code, flag, nums]):
        return jsonify({'success': False, 'message': 'Missing required fields'}), 400

    if country_code and not country_code.startswith('+') and country_code.isdigit():
        country_code = '+' + country_code

    added = 0
    with numbers_lock:
        for n in nums:
            n_clean = str(n).strip()
            if n_clean:
                item_id = "num_" + hashlib.md5((country_code + n_clean + str(time.time())).encode()).hexdigest()[:8]
                numbers_data.append({
                    'id':             item_id,
                    'country':        normalize_country(country),
                    'countryCode':    country_code,
                    'flag':           flag,
                    'number':         n_clean,
                    'status':         status,
                    'supported_apps': supported,
                    'blocked_apps':   blocked,
                    'received_sms_count': 0
                })
                added += 1

    save_numbers()
    return jsonify({'success': True, 'message': f'Added {added} numbers', 'total': len(numbers_data)})

@app.route('/api/admin/numbers/<item_id>', methods=['PUT'])
def update_number(item_id):
    if not check_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    body = request.get_json(silent=True) or {}
    updated = False

    with numbers_lock:
        for item in numbers_data:
            if item.get('id') == item_id or item.get('number') == item_id:
                if 'country' in body:
                    item['country'] = normalize_country(body['country'])
                if 'countryCode' in body:
                    cc = str(body['countryCode']).strip()
                    if cc and not cc.startswith('+') and cc.isdigit():
                        cc = '+' + cc
                    item['countryCode'] = cc
                if 'flag' in body:
                    item['flag'] = body['flag']
                if 'number' in body:
                    item['number'] = str(body['number']).strip()
                if 'status' in body:
                    item['status'] = body['status']
                if 'supported_apps' in body:
                    item['supported_apps'] = body['supported_apps']
                if 'blocked_apps' in body:
                    item['blocked_apps'] = body['blocked_apps']
                updated = True
                break

    if not updated:
        return jsonify({'success': False, 'message': 'Number not found'}), 404

    save_numbers()
    return jsonify({'success': True, 'message': 'Number updated successfully'})

@app.route('/api/admin/numbers/<item_id>', methods=['DELETE'])
def delete_number(item_id):
    if not check_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    global numbers_data
    with numbers_lock:
        before = len(numbers_data)
        numbers_data = [n for n in numbers_data if n.get('id') != item_id and n.get('number') != item_id]
        deleted = before - len(numbers_data)

    if deleted == 0:
        return jsonify({'success': False, 'message': 'Number not found'}), 404

    save_numbers()
    return jsonify({'success': True, 'message': 'Number deleted', 'total': len(numbers_data)})

@app.route('/api/admin/numbers/clear', methods=['POST'])
def clear_all_numbers():
    if not check_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    global numbers_data
    with numbers_lock:
        numbers_data = []
    save_numbers()
    return jsonify({'success': True, 'message': 'All numbers cleared'})

@app.route('/api/admin/numbers/country/<country_code>', methods=['DELETE'])
def delete_country_numbers(country_code):
    if not check_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    global numbers_data
    c_clean = country_code.strip().upper()
    with numbers_lock:
        before = len(numbers_data)
        numbers_data = [n for n in numbers_data if n['countryCode'].upper() != c_clean and n['country'].upper() != c_clean]
        deleted = before - len(numbers_data)

    save_numbers()
    return jsonify({'success': True, 'deleted': deleted, 'total': len(numbers_data)})

@app.route('/api/admin/notifications/send', methods=['POST'])
def send_custom_notification():
    """Admin endpoint to broadcast push notification to users' devices"""
    if not check_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    body = request.get_json(silent=True) or {}
    title = body.get('title')
    text = body.get('body')
    target_num = body.get('number')
    app_id = body.get('app_id', 'general')

    if not title or not text:
        return jsonify({'success': False, 'message': 'Title and Body are required'}), 400

    shared_storage.broadcast_custom_notification(
        title=title,
        body=text,
        target_number=target_num,
        app_id=app_id
    )

    return jsonify({
        'success': True,
        'message': f'Notification broadcasted to {target_num or "all users"}'
    })

# ===== STARTUP =====
def _startup():
    load_numbers()

threading.Thread(target=_startup, daemon=True).start()

if __name__ == '__main__':
    logger.info(f"Starting FreeNumber API server on port {PORT}...")
    time.sleep(1)
    app.run(host='0.0.0.0', port=PORT, debug=False)
