"""Find the TELEGRAM_CHAT_ID to put in .env, and prove the bot can reach it.

    python scripts/telegram_setup.py           # list the chats the bot can see
    python scripts/telegram_setup.py --test    # send a test message to TELEGRAM_CHAT_ID

Getting a chat id is the one genuinely awkward part of Telegram setup, and it has
to be redone for every environment (your laptop, the server, each client). This
turns it into one command.

Note the asymmetry that trips everyone up: a bot can only message someone who has
messaged it first. If the list comes back empty, open the bot in Telegram and send
it /start - that is what makes the chat exist as far as the API is concerned.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.notifications.channels import TELEGRAM_TOKEN_PATTERN, redact

API = "https://api.telegram.org/bot{token}/{method}"


def call(method: str, payload: dict | None = None) -> dict:
    """Call a Bot API method. Never raises, and never returns the token in a message.

    Every failure becomes {"ok": False, "description": ...}, redacted, so a bad
    network or a malformed token cannot print the token in a traceback.
    """
    try:
        url = API.format(token=TELEGRAM_BOT_TOKEN, method=method)
        data = json.dumps(payload).encode() if payload else None
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"} if data else {}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            result = json.loads(body)
        except ValueError:
            return {"ok": False, "description": redact(f"HTTP {e.code}: {body[:200]}")}
        if isinstance(result, dict) and "description" in result:
            result["description"] = redact(result["description"])
        return result
    except Exception as e:  # noqa: BLE001 - reported as a failed call, redacted
        return {"ok": False, "description": redact(f"{type(e).__name__}: {e}")}


def describe_chat(chat: dict) -> str:
    name = (
        chat.get("title")
        or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
        or chat.get("username")
        or "-"
    )
    return f"  {str(chat['id']):<16} {chat.get('type', '?'):<12} {name}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--test", action="store_true", help="send a test message to TELEGRAM_CHAT_ID"
    )
    args = parser.parse_args()

    if not TELEGRAM_BOT_TOKEN:
        print(
            "error: TELEGRAM_BOT_TOKEN ayarlı değil.\n"
            "Telegram'da @BotFather ile konuş, /newbot ile bot oluştur, verdiği token'ı"
            " .env içine TELEGRAM_BOT_TOKEN olarak yaz.",
            file=sys.stderr,
        )
        return 1
    if not TELEGRAM_TOKEN_PATTERN.fullmatch(TELEGRAM_BOT_TOKEN):
        print(
            "error: TELEGRAM_BOT_TOKEN beklenen bicimde degil (<rakamlar>:<harf/rakam>, "
            "bosluk veya satir sonu olmadan). Deger guvenlik icin gosterilmiyor - "
            ".env'de tirnak icinde bosluk ya da gorunmeyen bir karakter olabilir.",
            file=sys.stderr,
        )
        return 1

    # getMe separates "the token is wrong" from "the chat is wrong" - the two
    # failures look similar from send_daily_alerts.py but have different fixes.
    me = call("getMe")
    if not me.get("ok"):
        print(f"error: token geçersiz görünüyor - {me.get('description')}", file=sys.stderr)
        return 1
    bot = me["result"]
    print(f"Bot: @{bot.get('username')} ({bot.get('first_name')})")

    if args.test:
        if not TELEGRAM_CHAT_ID:
            print("error: TELEGRAM_CHAT_ID ayarlı değil.", file=sys.stderr)
            return 1
        result = call(
            "sendMessage",
            {"chat_id": TELEGRAM_CHAT_ID, "text": "EO-Churn bildirim testi - bu mesajı görüyorsan kurulum tamam."},
        )
        if result.get("ok"):
            print(f"Test mesajı gönderildi -> chat {TELEGRAM_CHAT_ID}")
            return 0
        print(f"error: gönderilemedi - {result.get('description')}", file=sys.stderr)
        if "chat not found" in str(result.get("description", "")).lower():
            print(
                "\nBu hata neredeyse her zaman şu ikisinden biri:\n"
                "  1. Bota hiç yazmadın. Telegram'da botu aç, /start bas, tekrar dene.\n"
                "  2. TELEGRAM_CHAT_ID yanlış. Bu komutu --test olmadan çalıştır, doğru id'yi listeler.",
                file=sys.stderr,
            )
        return 1

    updates = call("getUpdates")
    if not updates.get("ok"):
        print(f"error: getUpdates başarısız - {updates.get('description')}", file=sys.stderr)
        return 1

    chats = {}
    for update in updates.get("result", []):
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if chat:
                chats[chat["id"]] = chat

    if not chats:
        print(
            f"\nHenüz hiçbir sohbet görünmüyor.\n\n"
            f"Telegram'da @{bot.get('username')} botunu aç ve /start bas "
            f"(gruba göndereceksen botu gruba ekleyip grupta bir mesaj yaz),\n"
            f"sonra bu komutu tekrar çalıştır.\n\n"
            f"Bir bot, kendisine önce yazılmamış birine mesaj gönderemez - "
            f"\"chat not found\" hatasının sebebi budur.",
            file=sys.stderr,
        )
        return 1

    print("\nBulunan sohbetler:")
    print(f"  {'ID':<16} {'tip':<12} isim")
    for chat in chats.values():
        print(describe_chat(chat))

    first = next(iter(chats))
    print(f"\n.env içine:  TELEGRAM_CHAT_ID={first}")
    print("Sonra doğrula:  python scripts/telegram_setup.py --test")
    if TELEGRAM_CHAT_ID and int(TELEGRAM_CHAT_ID) not in chats:
        print(
            f"\nuyarı: .env'deki TELEGRAM_CHAT_ID={TELEGRAM_CHAT_ID} bu listede yok - "
            f"\"chat not found\" hatasının sebebi bu.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
