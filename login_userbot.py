#!/usr/bin/env python3
"""
login_userbot.py — USERBOT_SESSION generator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ye script ek baar interactively chalti hai aur aapko USERBOT_SESSION string
deti hai jo .env me daalni hoti hai. Uske baad saari user-facing messages
(bot ki jagah) usi user account se jayengi.

Run:
    python3 login_userbot.py

Steps:
    1. Phone number daalein (international format, e.g. +919876543210)
    2. Telegram par aaya OTP code daalein
    3. (Agar 2FA on hai) password daalein
    4. Print hui USERBOT_SESSION=... line ko .env me copy kar lein
"""

import sys

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneNumberInvalidError,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import os

API_ID = int(os.getenv("API_ID", "6").strip() or 6)
API_HASH = os.getenv("API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e").strip()


def main() -> int:
    print("=" * 60)
    print("  USERBOT SESSION GENERATOR")
    print("=" * 60)
    print(f"API_ID  : {API_ID}")
    print(f"API_HASH: {API_HASH[:8]}... (from env/default)")
    print()

    phone = input("📱 Phone number (e.g. +919876543210): ").strip()
    if not phone:
        print("❌ Phone number required.")
        return 1

    client = TelegramClient(StringSession(), API_ID, API_HASH)

    import asyncio

    async def _login() -> str:
        await client.connect()
        await client.send_code_request(phone)
        code = input("🔑 OTP code (Telegram se aaya hua): ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            pw = input("🔐 2FA password: ").strip()
            await client.sign_in(password=pw)
        except PhoneCodeInvalidError:
            print("❌ Galat code.")
            sys.exit(1)
        except PhoneCodeExpiredError:
            print("❌ Code expire ho gaya. Dobara run karein.")
            sys.exit(1)
        me = await client.get_me()
        print()
        print(f"✅ Logged in as: {me.first_name} (@{me.username}) [id={me.id}]")
        return client.session.save()

    try:
        session_string = asyncio.run(_login())
    except PhoneNumberInvalidError:
        print("❌ Invalid phone number.")
        return 1
    except Exception as ex:
        print(f"❌ Login failed: {ex}")
        return 1
    finally:
        try:
            client.disconnect()
        except Exception:
            pass

    print()
    print("=" * 60)
    print("👇 Neeche wali line copy karke .env me paste karein:")
    print("=" * 60)
    print()
    print(f"USERBOT_SESSION={session_string}")
    print()
    print("⚠️  Ye session string SECRET hai — kisi ko share mat karein, git me commit mat karein.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
