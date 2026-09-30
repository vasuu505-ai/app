# FreeNumber Backend — Koyeb Deployment

## 📁 Backend Files (Koyeb par upload karo)
```
backend/
├── main.py              ← Flask API server (entry point)
├── panel1_scraper.py    ← Panel 1 scraper (login-based)
├── scrape.py            ← Panel 2 scraper (token-based)
├── shared_storage.py    ← In-memory OTP storage
├── numbers_data.json    ← Numbers database
├── requirements.txt     ← Python dependencies
├── Procfile             ← Gunicorn start command
└── runtime.txt          ← Python version
```

---

## 🚀 Step-by-Step Koyeb Deployment

### Step 1: GitHub Repo Banao
1. GitHub par nayi repo banao — naam: `freenumber-backend`
2. Backend folder ki saari files upload karo
3. Push to main branch

### Step 2: Koyeb Dashboard
1. https://app.koyeb.com → Sign up / Login
2. **"Create Service"** click karo
3. **"Web Service"** choose karo
4. **"GitHub"** connect karo

### Step 3: Build & Run Settings
| Setting | Value |
|---|---|
| **Repository** | `your-username/freenumber-backend` |
| **Branch** | `main` |
| **Build command** | `pip install -r requirements.txt` |
| **Run command** | `gunicorn main:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120` |
| **Port** | `8080` |

### Step 4: ⚠️ Environment Variables (MUST SET)
Koyeb Dashboard → Service → **Environment** tab mein ye sab add karo:

| Variable Name | Value | Description |
|---|---|---|
| `ADMIN_PASSWORD` | `apna_strong_password` | Admin panel ka password |
| `PANEL1_USERNAME` | `Vasu24` | Panel 1 ka login username |
| `PANEL1_PASSWORD` | `Vasu24` | Panel 1 ka login password |
| `S1T_API_TOKEN` | `SFFRRzRSQkm...` | Panel 2 ka API token |
| `S1T_BASE_URL` | `http://51.77.216.195/crapi/mait` | Panel 2 ka API base URL |

### Step 5: Health Check
- **Path:** `/health`
- **Port:** `8080`
- **Protocol:** HTTP

### Step 6: Deploy!
**Deploy** button dabao. 2-3 minute mein URL milega jaise:
```
https://freenumber-xxxx-yourname.koyeb.app
```

### Step 7: Backend Test Karo
Browser mein kholo:
- `https://your-url.koyeb.app/health` → `{"status":"ok","numbers":1,"otps":0}`
- `https://your-url.koyeb.app/api/numbers` → numbers list

---

## 🌐 Frontend Setup (config.js)

Backend URL milne ke baad frontend ke `config.js` mein daalo:

```js
window.API_BASE = 'https://freenumber-xxxx-yourname.koyeb.app';
```

Frontend ko kahi bhi host kar sakte ho:
- **GitHub Pages** (free)
- **Netlify** (free)
- **Vercel** (free)
- Ya kisi bhi web hosting par files upload karo

---

## 🔑 Admin Panel Use Karna

1. Browser mein `admin.html` kholo
2. Apna `ADMIN_PASSWORD` daalo
3. **Add Numbers** tab:
   - Country name: `India`
   - Country code: `IN` (2 letter ISO code)
   - Flag emoji: `🇮🇳`
   - Numbers: ek ek line mein numbers paste karo
4. Save karo — numbers backend par save ho jayenge

---

## ⚠️ Important Notes

### numbers_data.json Persistence
Koyeb free tier mein storage **ephemeral** (restart pe reset) hai.
Fix: Jo numbers add karo, unhe `numbers_data.json` mein commit bhi karo GitHub mein.
Restart hone par GitHub se woh auto load ho jayenge.

### Memory
OTPs sirf memory mein hain (restart pe clear). Ye normal hai — OTPs temporary hote hain.

### Free Tier Limits
- RAM: 512MB ✅
- CPU: 0.1 vCPU ✅
- Sleep mode: Free tier sleep ho sakta hai 30 min inactivity ke baad
  - Fix: UptimeRobot se /health endpoint ping karte raho (free)

---

## 📡 Dynamic API Endpoints Reference (AI Studio & Mobile App Ready)

| Method | Endpoint | Description |
|---|---|---|
| **GET** | `/admin` | **Web Admin Dashboard** (Passcode protected) |
| **GET** | `/health` | Server status, numbers & OTP count |
| **GET** | `/api/numbers` | Numbers list with `supported_apps`, `blocked_apps`, `received_sms_count`, `country`, `flag`, `status` |
| **GET** | `/api/numbers/{number}/messages` | **Specific number inbox** with `clean_otp`, `sender`, `app_id`, `message`, `timestamp` |
| **GET** | `/api/apps` | Popular apps matrix with `available_countries_count` and `status` |
| **GET** | `/api/otps` | Live OTPs with `clean_otp`, `app_id`, normalized `country`, `sender` |
| **GET** | `/api/otps/stream` | **Server-Sent Events (SSE)** real-time live stream |
| **POST** | `/api/notifications/subscribe` | Register device FCM token for push notifications |
| **POST** | `/api/admin/verify` | Verify admin passcode |
| **POST** | `/api/admin/numbers` | Add numbers with country, flag, supported apps, blocked apps |
| **PUT** | `/api/admin/numbers/{id}` | Edit number details, status & supported apps |
| **DELETE** | `/api/admin/numbers/{id}` | Delete specific number |
| **POST** | `/api/admin/notifications/send` | Admin broadcast push alert to users |
| **POST** | `/api/admin/numbers/clear` | Clear all numbers |
| **POST** | `/api/admin/otps/clear` | Clear all OTPs in storage |

