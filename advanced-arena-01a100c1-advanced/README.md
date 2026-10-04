# advacc — Modified Version (User-Account Delivery + MongoDB)

Ye `advanced` bot ka **naya modified version** hai. Do bade changes:

| # | Pehle (old) | Ab (new) |
|---|-------------|----------|
| 1 | User ko messages **bot** bhejta tha (welcome / broadcast / leave-recovery) | Ab saari user-facing messages ek real **user account** (Telethon) se jaati hain — bot sirf panel + join-request events ke liye hai |
| 2 | Database **PostgreSQL** tha (hardcoded localhost) | Database ab **MongoDB** hai (Atlas ya local, `.env` se configure) |

---

## Features (same as before, new delivery engine ke saath)

- User apna bot add karta hai (BotFather token) → panel se manage karta hai
- Channel add karo (forward message), welcome messages set karo (text/photo/video/document/album/buttons)
- **Koi bhi channel me join request aaye → user ko DM user account se jata hai**
  (default first message + saved welcome messages + live-chat button)
- Auto-approve toggle, Accept All, Pending list
- Broadcast to users (user account se), Leave-recovery DMs
- Subscription system (Basic/Pro), reminders, admin panel

---

## Setup (step-by-step)

### 1. Requirements
- Python 3.10+
- MongoDB (Atlas free cluster ya local `mongod`)

### 2. Install

```bash
git clone https://github.com/avinashraz19818/advacc.git
cd advacc
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. `.env` banao

```bash
cp .env.example .env
```

Ab `.env` me values bharein:

| Variable | Kya daalein |
|----------|-------------|
| `MAIN_BOT_TOKEN` | @BotFather se naya bot token |
| `MONGO_URI` | MongoDB Atlas SRV string (ya `mongodb://127.0.0.1:27017`) |
| `MONGO_DB` | Database naam (e.g. `advacc_bot`) |
| `ADMIN_USER_IDS` | Aapka Telegram user id (comma-sep for multiple) |
| `USERBOT_SESSION` | Step 4 se generate karein |
| `API_ID` / `API_HASH` | https://my.telegram.org (optional, default public values) |

> ⚠️ `.env` secret hai — kabhi commit mat karo (gitignore me hai).

### 4. User account session banao (IMPORTANT)

Messages user account se jaane ke liye ek logged-in session chahiye:

```bash
python3 login_userbot.py
```

Phone + OTP (+ 2FA) daalein → print hui line ko `.env` me paste karein:

```
USERBOT_SESSION=1BJWap1uBu...
```

Us user account ko apni **har channel me admin** banao (invite-requests approve + user ko DM kar sake).

### 5. Chalao

```bash
./start
# ya directly:
python3 advanced.py
```

Logs: `tail -f bot.log`

### 6. Verify karo

1. Bot ko `/start` do → panel aana chahiye
2. Bot ko apni channel ka admin banao → panel me **Add Channel** → channel ka message forward karo
3. **Set Message(s)** se welcome messages set karo
4. Kisi doosre account se channel join request bhejo
5. ✅ DM **user account se** aana chahiye (bot se nahi)

---

## Architecture

```
join request (channel)
      │  (sirf bot ko milta hai)
      ▼
advanced.py  ── handle_join_request()
      │
      ├── deliver_text()  ──► user_sender.py (Telethon USER ACCOUNT) ──► DM user ko
      │                         │ fail? → bot fallback
      ├── deliver_media() ──────┘
      │
      └── MongoDB (users / user_bots / subscriptions / channels / messages / join_requests ...)
```

- **Bot** = panel UI + join-request events + fallback delivery
- **User account** = asli delivery (welcome, broadcast, leave-recovery)
- **MongoDB** = saara data (`MONGO_URI` / `MONGO_DB`)

## Tests

```bash
pip install -r requirements-dev.txt
python3 -m unittest discover tests -v
```

Database layer aur delivery-routing ke tests `tests/` me hain (mongomock ke saath, bina real MongoDB ke).

---

## Notes / Troubleshooting

- `USERBOT_SESSION invalid/expired` → `python3 login_userbot.py` se naya session banao
- DM nahi ja rahe → user account ne privacy restrict ki ho sakti hai; bot fallback automatic hai
- Media nahi ja rahi → bot ko media tak access chahiye (bot admin ho channel me)
- FloodWait aaye to user_sender automatically wait karke retry karta hai
