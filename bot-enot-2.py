# ================================================================
# BossEpin Bot — PUBG Mobile UC Satış Botu (@bossepin_az_bot)
# ----------------------------------------------------------------
# Tam işlək Telegram Epin satış botu:
#   - EpinBulk API (https://epinbulk.shop/docs) ilə UC sifarişi
#   - Enot.io (https://docs.enot.io) ilə AVTOMATİK ödəniş linki
#     yaratma və webhook ilə avtomatik təsdiqləmə
#   - Bank kartına köçürmə ilə MANUAL ödəniş (qəbz şəkli + admin təsdiqi)
#     — istifadəçi ID daxil etdikdən sonra bu iki üsuldan birini seçir
#   - Flask "keep-alive" server (UptimeRobot üçün)
#   - Çökmə zamanı 10 saniyə sonra avtomatik yenidən başlama
#   - Bütün xətalar log.txt-ə yazılır
#
# QEYD (VACİB): EpinBulk-un rəsmi sənədinə görə autentifikasiya
# "Bearer Token" ilə DEYİL, "X-API-KEY" HTTP header-i ilədir.
# Kodda düzgün üsul istifadə olunub (bax: epinbulk_basliq()).
#
# ENOT.IO QEYDLƏRİ:
#   - Kabinet: https://cabinet.enot.io  (mağaza yaradıb moderasiyadan keçirin)
#   - shop_id: mağazanın UUID-si (kabinetdə "Идентификатор кассы")
#   - ENOT_API_KEY: mağazanın "Пароль #1" (invoice yaratmaq üçün x-api-key)
#   - ENOT_WEBHOOK_SECRET: mağazanın "Пароль #2 / Дополнительный ключ"
#     (yalnız webhook imzasını yoxlamaq üçün, HEÇ VAXT sorğuda göndərilmir)
#   - Enot.io invoice currency olaraq RUB/USD/EUR/UAH qəbul edir (AZN yoxdur),
#     ona görə qiymət AZN-də göstərilir, arxada USD-yə çevrilib göndərilir
#     (kartla/kriptoyla USD-ə yaxın USDT məzənnəsi ilə ödənilir).
#
# Lazımi kitabxanalar:
#   pip install pyTelegramBotAPI requests flask
# ================================================================

import os
import sys
import uuid
import time
import json
import hmac
import hashlib
import sqlite3
import logging
import threading
from datetime import datetime

import requests
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ----------------------------------------------------------------
# LOG AYARLARI — bütün xətalar və mühüm hadisələr log.txt-ə yazılır
# ----------------------------------------------------------------
logger = logging.getLogger("bossepin")
logger.setLevel(logging.INFO)

_fayl_handler = logging.FileHandler("log.txt", encoding="utf-8")
_fayl_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fayl_handler)

_konsol_handler = logging.StreamHandler()
_konsol_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_konsol_handler)

# ----------------------------------------------------------------
# AYARLAR — BURANI ÖZ MƏLUMATLARINIZLA DOLDURUN
# ----------------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "BOTFATHER_TOKEN")
EPINBULK_API_KEY = "gbk_live_7l9o4hddyrfj2cc281hdlqxwojfzgha3x0uke8zciaugwy5j"
ADMIN_ID = 7262941693

# /admin əmrindən əvvəl bu istifadəçi adı/parol tələb olunur.
ADMIN_USERNAME = "XRPTON8841"
ADMIN_PASSWORD = "SHIBADA7f92LTC"

EPINBULK_BASE_URL = "https://epinbulk.shop/api/v1"

# ----------------------------------------------------------------
# ENOT.IO — AVTOMATİK ÖDƏNİŞ (kart / SBP / kripto)
# Kabinetdən (https://cabinet.enot.io) mağaza yaradıb aşağıdakıları doldurun.
# ----------------------------------------------------------------
ENOT_SHOP_ID = os.environ.get("ENOT_SHOP_ID", "SIZIN_SHOP_ID")            # DƏQİQLƏŞDİRİN
ENOT_API_KEY = os.environ.get("ENOT_API_KEY", "SIZIN_API_KEY")            # "Пароль #1" — invoice yaratmaq üçün
ENOT_WEBHOOK_SECRET = os.environ.get("ENOT_WEBHOOK_SECRET", "SIZIN_WEBHOOK_SECRET")  # "Пароль #2" — yalnız imza yoxlamaq üçün
ENOT_BASE_URL = "https://api.enot.io"
ENOT_INVOICE_CURRENCY = "USD"     # Enot.io: RUB/USD/EUR/UAH dəstəkləyir, AZN yoxdur
AZN_USD_MEZENNE = 1.70            # 1 USD ≈ 1.70 AZN (lazım olsa yeniləyin)

# Botun 24/7 işlədiyi serverin xarici (https) ünvanı.
# Məsələn: https://istifadeciadi.pythonanywhere.com
WEBHOOK_PUBLIC_URL = "https://sizin-domain.pythonanywhere.com"

PUL_VAHIDI = "AZN"          # Satış qiymətlərinizin valyutası (lazım olsa dəyişin)
MIN_BALANCE_ALERT = 5.0     # USD — EpinBulk balansı bundan az olanda admin xəbərdar edilir

# Manual (əl ilə) ödəniş — kart/bank köçürməsi. Öz məlumatlarınızla doldurun.
MANUAL_PAYMENT_INFO = {
    "bank_adi": "Kapital Bank",
    "kart_nomresi": "4169 7388 **** ****",   # öz kart nömrənizi yazın
    "kart_sahibi": "Selim Muradov",          # kartın üzərindəki ad-soyad
}

DB_PATH = "bossepin.db"


def azn_to_usd(azn_meblegi):
    """Enot.io invoice-i USD-də yaradılır (AZN dəstəklənmir) — sabit məzənnə ilə çevrilir."""
    return round(float(azn_meblegi) / AZN_USD_MEZENNE, 2)

# ----------------------------------------------------------------
# SABİT QİYMƏT SİYAHISI
# epinbulk_id sahələri boşdur. Doldurmaq üçün:
#   1) EPINBULK_API_KEY-i yuxarıda doldurun
#   2) terminalda: python3 bot.py --mehsullari-tap
#   3) çıxan siyahıdan uyğun ID-ləri aşağıya yapışdırın
# ----------------------------------------------------------------
products = {
    "60_uc": {"ad": "60 UC", "qiymet": 2.00, "epinbulk_id": ""},
    "180_uc": {"ad": "180 UC", "qiymet": 5.00, "epinbulk_id": ""},
    "325_uc": {"ad": "325 UC", "qiymet": 8.50, "epinbulk_id": ""},
    "385_uc": {"ad": "385 UC", "qiymet": 11.00, "epinbulk_id": ""},
    "660_uc": {"ad": "660 UC", "qiymet": 16.50, "epinbulk_id": ""},
    "720_uc": {"ad": "720 UC", "qiymet": 18.00, "epinbulk_id": ""},
    "780_uc": {"ad": "780 UC", "qiymet": 20.00, "epinbulk_id": ""},
    "985_uc": {"ad": "985 UC", "qiymet": 25.00, "epinbulk_id": ""},
    "1320_uc": {"ad": "1320 UC", "qiymet": 32.00, "epinbulk_id": ""},
    "1800_uc": {"ad": "1800 UC", "qiymet": 41.00, "epinbulk_id": ""},
    "1980_uc": {"ad": "1980 UC", "qiymet": 46.00, "epinbulk_id": ""},
    "2125_uc": {"ad": "2125 UC", "qiymet": 48.00, "epinbulk_id": ""},
    "2460_uc": {"ad": "2460 UC", "qiymet": 56.00, "epinbulk_id": ""},
    "3120_uc": {"ad": "3120 UC", "qiymet": 71.00, "epinbulk_id": ""},
    "3850_uc": {"ad": "3850 UC", "qiymet": 82.00, "epinbulk_id": ""},
    "4030_uc": {"ad": "4030 UC", "qiymet": 84.00, "epinbulk_id": ""},
    "5170_uc": {"ad": "5170 UC", "qiymet": 112.00, "epinbulk_id": ""},
    "5650_uc": {"ad": "5650 UC", "qiymet": 118.00, "epinbulk_id": ""},
    "8100_uc": {"ad": "8100 UC", "qiymet": 156.00, "epinbulk_id": ""},
    "9900_uc": {"ad": "9900 UC", "qiymet": 193.00, "epinbulk_id": ""},
    "11950_uc": {"ad": "11950 UC", "qiymet": 233.00, "epinbulk_id": ""},
    "16200_uc": {"ad": "16200 UC", "qiymet": 310.00, "epinbulk_id": ""},
}

# ----------------------------------------------------------------
# SİFARİŞ STATUSLARININ İZAHI
# ----------------------------------------------------------------
VEZIYYET_METNLERI = {
    "ODENIS_GOZLENILIR": "Ödəniş gözlənilir",
    "ODENILDI": "Ödənildi, icra olunur",
    "EPINBULK_GOZLENILIR": "İcra olunur",
    "TAMAMLANDI": "Tamamlandı",
    "XETA": "Xəta",
}
VEZIYYET_EMOJILERI = {
    "ODENIS_GOZLENILIR": "⏳",
    "ODENILDI": "⏳",
    "EPINBULK_GOZLENILIR": "⏳",
    "TAMAMLANDI": "✅",
    "XETA": "❌",
}


def vaziyyet_metni(s):
    return VEZIYYET_METNLERI.get(s, s)


def vaziyyet_emoji(s):
    return VEZIYYET_EMOJILERI.get(s, "❔")


# ----------------------------------------------------------------
# VERİLƏNLƏR BAZASI (SQLite)
# ----------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sifarisler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref TEXT UNIQUE,
            user_id INTEGER,
            username TEXT,
            product_key TEXT,
            product_ad TEXT,
            satis_qiymeti REAL,
            player_id TEXT,
            status TEXT,
            odenis_novu TEXT DEFAULT 'enot',
            enot_invoice_id TEXT,
            manual_qebz_file_id TEXT,
            epinbulk_order_id TEXT,
            epinbulk_qiymet REAL,
            netice TEXT,
            yaradilma_tarixi TEXT
        )
    """)
    # Köhnə verilənlər bazasında bu sütunlar olmaya bilər — varsa xəta
    # sükutla keçilir (sütun artıq mövcuddur deməkdir).
    for sutun, tip in (("odenis_novu", "TEXT DEFAULT 'enot'"), ("enot_invoice_id", "TEXT"), ("manual_qebz_file_id", "TEXT")):
        try:
            conn.execute(f"ALTER TABLE sifarisler ADD COLUMN {sutun} {tip}")
        except sqlite3.OperationalError:
            pass

    # Botdan (admin panelindən) dəyişilə bilən ayarlar (məs. manual ödəniş kartı)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ayarlar (
            achar TEXT PRIMARY KEY,
            deyer TEXT
        )
    """)
    conn.commit()
    conn.close()


def ayar_al(achar, defolt=None):
    conn = db_baglan()
    row = conn.execute("SELECT deyer FROM ayarlar WHERE achar=?", (achar,)).fetchone()
    conn.close()
    return row["deyer"] if row else defolt


def ayar_yaz(achar, deyer):
    conn = db_baglan()
    conn.execute(
        "INSERT INTO ayarlar (achar, deyer) VALUES (?,?) "
        "ON CONFLICT(achar) DO UPDATE SET deyer=excluded.deyer",
        (achar, deyer),
    )
    conn.commit()
    conn.close()


def manual_odenis_melumati_al():
    """Kart məlumatını verilənlər bazasından oxuyur (admin dəyişibsə),
    yoxdursa faylın başındakı MANUAL_PAYMENT_INFO defolt dəyərlərini qaytarır."""
    return {
        "bank_adi": ayar_al("manual_bank_adi", MANUAL_PAYMENT_INFO["bank_adi"]),
        "kart_nomresi": ayar_al("manual_kart_nomresi", MANUAL_PAYMENT_INFO["kart_nomresi"]),
        "kart_sahibi": ayar_al("manual_kart_sahibi", MANUAL_PAYMENT_INFO["kart_sahibi"]),
    }


def db_baglan():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ----------------------------------------------------------------
# TELEGRAM BOT VƏ FLASK
# ----------------------------------------------------------------
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)

user_states = {}  # {telegram_user_id: {"stage": "...", "product_key": "..."}}
admin_giris_edenler = set()  # botun bu iş prosesi ərzində uğurla login olmuş admin user_id-ləri


@app.route("/")
def anasehife():
    return "BossEpin Bot is alive"


# ----------------------------------------------------------------
# EPINBULK API FUNKSİYALARI
# ----------------------------------------------------------------
def epinbulk_basliq():
    return {
        "X-API-KEY": EPINBULK_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def epinbulk_balans():
    """EpinBulk hesabının cari balansını (USD) qaytarır. Xəta olarsa None."""
    try:
        r = requests.get(f"{EPINBULK_BASE_URL}/getMe", headers=epinbulk_basliq(), timeout=15)
        data = r.json()
        if data.get("success"):
            return float(data["data"]["balance"])
        logger.error(f"EpinBulk balans xətası: {data}")
        return None
    except Exception as e:
        logger.error(f"EpinBulk balans sorğusunda xəta: {e}")
        return None


def epinbulk_sifaris_yarat(epinbulk_id, player_id, order_ref):
    """EpinBulk-da UC sifarişi (TOPUP) yaradır."""
    payload = {
        "product_id": int(epinbulk_id),
        "qty": 1,
        "player_id": str(player_id),
        "client_order_id": order_ref,
    }
    headers = epinbulk_basliq()
    headers["X-Idempotency-Key"] = order_ref
    try:
        r = requests.post(f"{EPINBULK_BASE_URL}/order", headers=headers, json=payload, timeout=30)
        return r.json()
    except Exception as e:
        logger.error(f"EpinBulk sifariş yaradılarkən xəta ({order_ref}): {e}")
        return {"success": False, "error": {"message": str(e)}}


def epinbulk_sifaris_statusu(epinbulk_order_id):
    try:
        r = requests.get(
            f"{EPINBULK_BASE_URL}/order-status/{epinbulk_order_id}",
            headers=epinbulk_basliq(), timeout=15,
        )
        return r.json()
    except Exception as e:
        logger.error(f"EpinBulk status sorğusunda xəta ({epinbulk_order_id}): {e}")
        return {"success": False, "error": {"message": str(e)}}


def mehsullari_tap_ve_cap_et():
    """Bir dəfəlik köməkçi: PUBG Mobile üçün EpinBulk-dakı bütün TOPUP
    məhsullarını (ID, ad, qiymət) çap edir.
    Terminalda çağırın: python3 bot.py --mehsullari-tap
    """
    try:
        r = requests.get(
            f"{EPINBULK_BASE_URL}/products",
            headers=epinbulk_basliq(),
            params={"game": "PUBG", "type": "topup", "per_page": 100},
            timeout=20,
        )
        data = r.json()
        if not data.get("success"):
            print("Xəta:", data)
            return
        print(f"{'ID':<8}{'Qiymət (USD)':<15}Ad")
        print("-" * 60)
        for p in data.get("data", []):
            print(f"{p['id']:<8}{p['price']:<15}{p['name']}")
    except Exception as e:
        print("Sorğu zamanı xəta:", e)


# ----------------------------------------------------------------
# ENOT.IO ÖDƏNİŞ FUNKSİYALARI (avtomatik — https://docs.enot.io)
# ----------------------------------------------------------------
def enot_invoice_yarat(order_ref, mebleg_azn, tesvir):
    """Enot.io-da invoice (ödəniş linki) yaradır. Uğurlu olarsa (url, invoice_id)
    qaytarır, xəta olarsa (None, None)."""
    mebleg_usd = azn_to_usd(mebleg_azn)
    payload = {
        "amount": mebleg_usd,
        "order_id": order_ref,
        "currency": ENOT_INVOICE_CURRENCY,
        "shop_id": ENOT_SHOP_ID,
        "comment": tesvir,
        "hook_url": f"{WEBHOOK_PUBLIC_URL}/enot-webhook",
        "expire": 60,  # dəqiqə
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": ENOT_API_KEY,
    }
    try:
        r = requests.post(f"{ENOT_BASE_URL}/invoice/create", json=payload, headers=headers, timeout=20)
        data = r.json()
        if data.get("status") == 200 and data.get("data"):
            return data["data"].get("url"), data["data"].get("id")
        logger.error(f"Enot.io invoice xətası ({order_ref}): {data}")
        return None, None
    except Exception as e:
        logger.error(f"Enot.io invoice yaradılarkən xəta ({order_ref}): {e}")
        return None, None


def enot_webhook_imzasi_dogrudur(hook_body_dict, header_signature):
    """Enot.io sənədinə əsasən: hook body-si açar adına görə (a-zA-Z) sıralanır,
    JSON-a çevrilir (slash escape edilmədən) və HMAC-SHA256 ilə imzalanır."""
    if not header_signature:
        return False
    try:
        sorted_json = json.dumps(hook_body_dict, sort_keys=True, separators=(", ", ": "), ensure_ascii=False)
        calc_sign = hmac.new(
            ENOT_WEBHOOK_SECRET.encode("utf-8"),
            msg=sorted_json.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(header_signature, calc_sign)
    except Exception as e:
        logger.error(f"Enot.io webhook imza yoxlanışında xəta: {e}")
        return False


# ----------------------------------------------------------------
# SİFARİŞİN EMALI
# ----------------------------------------------------------------
def sifaris_teslim_et(user_id, sifaris_id, data):
    """EpinBulk sifarişi COMPLETED olanda müştəriyə nəticəni göndərir.
    Məhsul növündən asılı olaraq ya kod (voucher), ya da birbaşa
    ID-yə yükləmə təsdiqi göndərilir (PUBG Mobile UC adətən TOPUP
    tipindədir və birbaşa ID-yə yüklənir, kod qaytarmır)."""
    teslimat = data.get("delivery")
    if teslimat:
        kod_metni = "\n".join(teslimat) if isinstance(teslimat, list) else str(teslimat)
        mesaj = f"✅ Kodunuz: {kod_metni}"
    else:
        mehsul_adi = data.get("product_name", "UC")
        oyunçu_id = data.get("player_id", "-")
        mesaj = f"✅ Sifarişiniz tamamlandı! {mehsul_adi} hesabınıza (ID: {oyunçu_id}) uğurla yükləndi 🎮"

    bot.send_message(user_id, mesaj)
    conn = db_baglan()
    conn.execute("UPDATE sifarisler SET status='TAMAMLANDI', netice=? WHERE id=?", (mesaj, sifaris_id))
    conn.commit()
    conn.close()


def odenis_ugurlu_emal_et(order_ref):
    """Yığım-dan uğurlu ödəniş siqnalı gələndə çağırılır."""
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref = ?", (order_ref,)).fetchone()
    if not row:
        logger.error(f"Naməlum order_ref üçün ödəniş bildirişi gəldi: {order_ref}")
        conn.close()
        return
    if row["status"] != "ODENIS_GOZLENILIR":
        conn.close()
        return  # artıq emal olunub (təkrar webhook)

    conn.execute("UPDATE sifarisler SET status='ODENILDI' WHERE order_ref=?", (order_ref,))
    conn.commit()
    conn.close()

    product = products.get(row["product_key"])
    epinbulk_id = product.get("epinbulk_id") if product else None

    if not epinbulk_id:
        logger.error(f"'{row['product_key']}' üçün epinbulk_id doldurulmayıb!")
        bot.send_message(row["user_id"], "❌ Texniki xəta baş verdi. Admin sizinlə əlaqə saxlayacaq.")
        bot.send_message(
            ADMIN_ID,
            f"‼️ '{row['product_key']}' üçün epinbulk_id boşdur — sifariş #{row['id']} icra oluna bilmədi.",
        )
        conn = db_baglan()
        conn.execute("UPDATE sifarisler SET status='XETA' WHERE order_ref=?", (order_ref,))
        conn.commit()
        conn.close()
        return

    netice = epinbulk_sifaris_yarat(epinbulk_id, row["player_id"], order_ref)

    if not netice.get("success"):
        xeta = netice.get("error", {}).get("message", "naməlum xəta")
        logger.error(f"EpinBulk sifariş xətası ({order_ref}): {xeta}")
        conn = db_baglan()
        conn.execute("UPDATE sifarisler SET status='XETA', netice=? WHERE order_ref=?", (xeta, order_ref))
        conn.commit()
        conn.close()
        bot.send_message(
            row["user_id"],
            "❌ Sifarişiniz icra olunarkən xəta baş verdi.\nAdmin sizinlə tezliklə əlaqə saxlayacaq.",
        )
        bot.send_message(ADMIN_ID, f"‼️ Sifariş #{row['id']} EpinBulk xətası: {xeta}\nÖdəniş geri qaytarılmalıdır.")
        return

    data = netice["data"]
    yeni_status = "TAMAMLANDI" if data.get("status") == "COMPLETED" else "EPINBULK_GOZLENILIR"

    conn = db_baglan()
    conn.execute(
        "UPDATE sifarisler SET epinbulk_order_id=?, epinbulk_qiymet=?, status=? WHERE order_ref=?",
        (str(data.get("order_id", "")), float(data.get("price", 0) or 0), yeni_status, order_ref),
    )
    conn.commit()
    conn.close()

    if yeni_status == "TAMAMLANDI":
        sifaris_teslim_et(row["user_id"], row["id"], data)
    else:
        bot.send_message(row["user_id"], "✅ Ödənişiniz qəbul edildi! Sifarişiniz icra olunur ⏳")


def gozleyen_sifarisi_yoxla(row):
    """EPINBULK_GOZLENILIR statusundakı sifarişin son vəziyyətini yoxlayır."""
    netice = epinbulk_sifaris_statusu(row["epinbulk_order_id"])
    if not netice.get("success"):
        return
    data = netice.get("data", {})
    status = data.get("status")

    if status == "COMPLETED":
        conn = db_baglan()
        conn.execute(
            "UPDATE sifarisler SET epinbulk_qiymet=? WHERE id=?",
            (float(data.get("price", row["epinbulk_qiymet"] or 0) or 0), row["id"]),
        )
        conn.commit()
        conn.close()
        sifaris_teslim_et(row["user_id"], row["id"], data)
    elif status in ("FAILED", "CANCELED"):
        conn = db_baglan()
        conn.execute("UPDATE sifarisler SET status='XETA', netice=? WHERE id=?", (status, row["id"]))
        conn.commit()
        conn.close()
        bot.send_message(row["user_id"], "❌ Sifarişiniz icra oluna bilmədi. Admin sizinlə əlaqə saxlayacaq.")
        bot.send_message(ADMIN_ID, f"‼️ Sifariş #{row['id']} {status} oldu — geri ödəmə lazımdır.")


# ----------------------------------------------------------------
# FLASK: ENOT.IO WEBHOOK (avtomatik ödəniş təsdiqi)
# ----------------------------------------------------------------
@app.route("/enot-webhook", methods=["POST"])
def enot_webhook():
    xam = request.get_data(as_text=True)
    logger.info(f"Enot.io webhook alındı: {xam}")

    try:
        data = request.get_json(force=True)
    except Exception as e:
        logger.error(f"Webhook JSON oxuna bilmədi: {e}")
        return jsonify({"ok": False}), 400

    imza = request.headers.get("x-api-sha256-signature", "")
    if not enot_webhook_imzasi_dogrudur(data, imza):
        logger.error(f"Enot.io webhook imzası yanlışdır! Gövdə: {xam}")
        return jsonify({"ok": False}), 401

    order_ref = data.get("order_id")
    status = str(data.get("status", "")).lower()

    if not order_ref:
        logger.error("Webhook-da order_id tapılmadı")
        return jsonify({"ok": False}), 400

    if status == "success":
        threading.Thread(target=odenis_ugurlu_emal_et, args=(order_ref,), daemon=True).start()
    elif status in ("fail", "expired"):
        conn = db_baglan()
        row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
        if row and row["status"] == "ODENIS_GOZLENILIR":
            conn.execute(
                "UPDATE sifarisler SET status='XETA', netice=? WHERE order_ref=?",
                (f"Enot.io: {status}", order_ref),
            )
            conn.commit()
            bot.send_message(
                row["user_id"],
                "❌ Ödəniş tamamlanmadı (vaxtı bitdi və ya rədd edildi). Yenidən sifariş verə bilərsiniz: /start",
            )
        conn.close()
        logger.info(f"Ödəniş uğursuz/vaxtı bitdi: {order_ref} -> {status}")
    else:
        logger.info(f"Enot.io webhook — izlənməyən status: {order_ref} -> {status}")

    return jsonify({"ok": True}), 200


# ----------------------------------------------------------------
# TELEGRAM: KÖMƏKÇİ KLAVİATURALAR
# ----------------------------------------------------------------
def basla_menyusu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🛒 PUBG UC Al", callback_data="menu_uc"),
        types.InlineKeyboardButton("💼 Balansım", callback_data="menu_balans"),
    )
    return kb


def mehsul_menyusu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    duymeler = [
        types.InlineKeyboardButton(f"{p['ad']} - {p['qiymet']:.2f} {PUL_VAHIDI}", callback_data=f"sec_{key}")
        for key, p in products.items()
    ]
    kb.add(*duymeler)
    kb.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="menu_basla"))
    return kb


# ----------------------------------------------------------------
# TELEGRAM: ƏMRLƏR
# ----------------------------------------------------------------
@bot.message_handler(commands=["start"])
def start_emri(message):
    metin = (
        "🎮 <b>PUBG MOBILE UC YÜKLƏMƏ</b> ⚡\n\n"
        "Ən sürətli və etibarlı UC yükləmə botuna xoş gəlmisiniz!\n"
        "Aşağıdakı düymələrdən birini seçin 👇"
    )
    bot.send_message(message.chat.id, metin, reply_markup=basla_menyusu())


@bot.message_handler(commands=["admin"])
def admin_emri(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔️ Bu əmrə icazəniz yoxdur.")
        return
    if message.from_user.id in admin_giris_edenler:
        admin_paneli_goster(message.chat.id)
        return
    user_states[message.from_user.id] = {"stage": "admin_giris_username_gozlenilir"}
    bot.send_message(message.chat.id, "🔐 İstifadəçi adını daxil edin:")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("stage") == "admin_giris_username_gozlenilir")
def admin_giris_username(message):
    if message.from_user.id != ADMIN_ID:
        return
    if message.text.strip() != ADMIN_USERNAME:
        user_states.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, "❌ İstifadəçi adı yanlışdır. Yenidən /admin yazın.")
        return
    user_states[message.from_user.id] = {"stage": "admin_giris_parol_gozlenilir"}
    bot.send_message(message.chat.id, "🔑 Parolu daxil edin:")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("stage") == "admin_giris_parol_gozlenilir")
def admin_giris_parol(message):
    if message.from_user.id != ADMIN_ID:
        return
    if message.text.strip() != ADMIN_PASSWORD:
        user_states.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, "❌ Parol yanlışdır. Yenidən /admin yazın.")
        return
    user_states.pop(message.from_user.id, None)
    admin_giris_edenler.add(message.from_user.id)
    bot.send_message(message.chat.id, "✅ Giriş uğurlu oldu.")
    admin_paneli_goster(message.chat.id)


def admin_girisi_yoxla(message):
    """/kart_bax, /kart_deyis və /mehsullari_tap kimi digər admin əmrləri üçün: giriş edilməyibsə xəbərdar edir."""
    if message.from_user.id != ADMIN_ID:
        return False
    if message.from_user.id not in admin_giris_edenler:
        bot.send_message(message.chat.id, "🔐 Əvvəlcə /admin yazıb giriş edin.")
        return False
    return True


def admin_paneli_goster(chat_id):
    conn = db_baglan()
    cemi_satis = conn.execute("SELECT COUNT(*) c FROM sifarisler WHERE status='TAMAMLANDI'").fetchone()["c"]
    cemi_qazanc = conn.execute(
        "SELECT COALESCE(SUM(satis_qiymeti - COALESCE(epinbulk_qiymet,0)),0) q "
        "FROM sifarisler WHERE status='TAMAMLANDI'"
    ).fetchone()["q"]
    son_10 = conn.execute("SELECT * FROM sifarisler ORDER BY id DESC LIMIT 10").fetchall()
    conn.close()

    balans = epinbulk_balans()
    balans_metni = f"{balans:.2f} USD" if balans is not None else "alına bilmədi ❌"

    metin = (
        "👑 <b>Admin Panel</b>\n\n"
        f"📦 Cəmi satış: <b>{cemi_satis}</b>\n"
        f"💰 Cəmi qazanc: <b>{cemi_qazanc:.2f} {PUL_VAHIDI}</b>\n"
        f"🏦 EpinBulk balansı: <b>{balans_metni}</b>\n\n"
        "🧾 <b>Son 10 sifariş:</b>\n"
    )
    if son_10:
        for s in son_10:
            metin += f"#{s['id']} | {s['product_ad']} | {vaziyyet_emoji(s['status'])} {vaziyyet_metni(s['status'])}\n"
    else:
        metin += "Hələ sifariş yoxdur."

    bot.send_message(chat_id, metin)

    if balans is not None and balans < MIN_BALANCE_ALERT:
        bot.send_message(ADMIN_ID, f"⚠️ Balans azdır! Cari balans: {balans:.2f} USD")


# ----------------------------------------------------------------
# ADMIN: MANUAL ÖDƏNİŞ KARTINI BOT İÇİNDƏN DƏYİŞMƏ
# ----------------------------------------------------------------
@bot.message_handler(commands=["kart_bax"])
def kart_bax(message):
    if not admin_girisi_yoxla(message):
        return
    kart = manual_odenis_melumati_al()
    bot.send_message(
        message.chat.id,
        f"🏦 Hazırkı manual ödəniş kartı:\n\n"
        f"Bank: <b>{kart['bank_adi']}</b>\n"
        f"Kart nömrəsi: <code>{kart['kart_nomresi']}</code>\n"
        f"Kart sahibi: <b>{kart['kart_sahibi']}</b>\n\n"
        f"Dəyişmək üçün: /kart_deyis",
    )


@bot.message_handler(commands=["kart_deyis"])
def kart_deyis_basla(message):
    if not admin_girisi_yoxla(message):
        return
    user_states[message.from_user.id] = {"stage": "admin_kart_bank_gozlenilir"}
    bot.send_message(message.chat.id, "🏦 Yeni bank adını yazın (məs: Kapital Bank):")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("stage") == "admin_kart_bank_gozlenilir")
def kart_deyis_bank(message):
    if message.from_user.id != ADMIN_ID:
        return
    user_states[message.from_user.id] = {
        "stage": "admin_kart_nomre_gozlenilir",
        "yeni_bank": message.text.strip(),
    }
    bot.send_message(message.chat.id, "💳 Yeni kart nömrəsini yazın (tam, 16 rəqəm):")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("stage") == "admin_kart_nomre_gozlenilir")
def kart_deyis_nomre(message):
    if message.from_user.id != ADMIN_ID:
        return
    state = user_states[message.from_user.id]
    state["stage"] = "admin_kart_ad_gozlenilir"
    state["yeni_nomre"] = message.text.strip()
    bot.send_message(message.chat.id, "👤 Kart sahibinin ad-soyadını yazın:")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("stage") == "admin_kart_ad_gozlenilir")
def kart_deyis_ad(message):
    if message.from_user.id != ADMIN_ID:
        return
    state = user_states.pop(message.from_user.id)

    ayar_yaz("manual_bank_adi", state["yeni_bank"])
    ayar_yaz("manual_kart_nomresi", state["yeni_nomre"])
    ayar_yaz("manual_kart_sahibi", message.text.strip())

    kart = manual_odenis_melumati_al()
    bot.send_message(
        message.chat.id,
        "✅ Kart məlumatı yeniləndi!\n\n"
        f"Bank: <b>{kart['bank_adi']}</b>\n"
        f"Kart nömrəsi: <code>{kart['kart_nomresi']}</code>\n"
        f"Kart sahibi: <b>{kart['kart_sahibi']}</b>",
    )


@bot.message_handler(commands=["mehsullari_tap"])
def mehsullari_tap_telegram(message):
    """/mehsullari_tap — terminala ehtiyac olmadan, telefondan EpinBulk-dakı
    PUBG Mobile TOPUP məhsullarının ID/qiymət/ad siyahısını Telegram-a göndərir."""
    if not admin_girisi_yoxla(message):
        return

    bot.send_message(message.chat.id, "⏳ EpinBulk-dan məhsullar çəkilir...")
    try:
        r = requests.get(
            f"{EPINBULK_BASE_URL}/products",
            headers=epinbulk_basliq(),
            params={"game": "PUBG", "type": "topup", "per_page": 100},
            timeout=20,
        )
        data = r.json()
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Sorğu zamanı xəta: {e}")
        return

    if not data.get("success"):
        bot.send_message(message.chat.id, f"❌ EpinBulk xətası: {data}")
        return

    siyahi = data.get("data", [])
    if not siyahi:
        bot.send_message(message.chat.id, "Heç bir məhsul tapılmadı.")
        return

    setirler = [f"{p['id']:<8}{str(p['price']):<10}{p['name']}" for p in siyahi]
    baslik = f"{'ID':<8}{'Qiymət':<10}Ad\n" + "-" * 40

    # Telegram mesaj limiti 4096 simvoldur — lazım gələrsə hissələrə bölürük
    par_olcusu = 40
    for i in range(0, len(setirler), par_olcusu):
        hisse = setirler[i:i + par_olcusu]
        metin = ("<code>" + baslik + "\n" if i == 0 else "<code>") + "\n".join(hisse) + "</code>"
        bot.send_message(message.chat.id, metin)

    bot.send_message(
        message.chat.id,
        "☝️ Yuxarıdakı ID-ləri kopyalayıb mənə göndər, `products` dict-inə uyğun paketlərə özüm yerləşdirim.",
    )


# ----------------------------------------------------------------
# TELEGRAM: ID NÖMRƏSİNİN QƏBULU
# ----------------------------------------------------------------
@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("stage") == "id_gozlenilir")
def id_qebul_et(message):
    user_id = message.from_user.id
    id_metni = message.text.strip()

    if not id_metni.isdigit() or not (5 <= len(id_metni) <= 15):
        bot.send_message(user_id, "⚠️ Zəhmət olmasa düzgün ID nömrəsi göndərin (yalnız rəqəmlər).")
        return

    try:
        state = user_states.get(user_id)
        key = state["product_key"]
        product = products[key]
        order_ref = uuid.uuid4().hex[:16]

        conn = db_baglan()
        conn.execute(
            "INSERT INTO sifarisler (order_ref, user_id, username, product_key, product_ad, satis_qiymeti, "
            "player_id, status, yaradilma_tarixi) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                order_ref, user_id, message.from_user.username or "", key, product["ad"], product["qiymet"],
                id_metni, "ODENIS_GOZLENILIR", datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        user_states.pop(user_id, None)

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("💳 Onlayn ödəniş (kart/SBP/kripto) — avtomatik", callback_data=f"odenis_enot_{order_ref}"),
            types.InlineKeyboardButton("🏦 Bank kartına köçürmə (manual)", callback_data=f"odenis_manual_{order_ref}"),
            types.InlineKeyboardButton("❌ Ləğv et", callback_data="legv"),
        )
        bot.send_message(
            user_id,
            f"📦 {product['ad']}\n🆔 ID: {id_metni}\n💵 Məbləğ: {product['qiymet']:.2f} {PUL_VAHIDI}\n\n"
            "Ödəniş üsulunu seçin:",
            reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"ID qəbulunda xəta: {e}")
        bot.send_message(user_id, "❌ Xəta baş verdi, yenidən cəhd edin.")


def odenis_novu_enot_basla(call, order_ref):
    """İstifadəçi 'Onlayn ödəniş' seçəndə çağırılır — Enot.io invoice yaradılır."""
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    if not row:
        conn.close()
        bot.answer_callback_query(call.id, "Sifariş tapılmadı", show_alert=True)
        return
    if row["status"] != "ODENIS_GOZLENILIR":
        conn.close()
        bot.answer_callback_query(call.id, "Bu sifariş artıq emal olunub.", show_alert=True)
        return
    conn.execute("UPDATE sifarisler SET odenis_novu='enot' WHERE order_ref=?", (order_ref,))
    conn.commit()
    conn.close()

    bot.answer_callback_query(call.id, "⏳ Ödəniş linki yaradılır...")

    odeniş_url, invoice_id = enot_invoice_yarat(order_ref, row["satis_qiymeti"], f"{row['product_ad']} — {row['player_id']}")

    if not odeniş_url:
        bot.send_message(call.message.chat.id, "❌ Ödəniş linki yaradılarkən xəta baş verdi. Bir az sonra yenidən cəhd edin.")
        bot.send_message(ADMIN_ID, f"‼️ Enot.io invoice yaradıla bilmədi — sifariş {order_ref}")
        return

    conn = db_baglan()
    conn.execute("UPDATE sifarisler SET enot_invoice_id=? WHERE order_ref=?", (invoice_id, order_ref))
    conn.commit()
    conn.close()

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("💳 Ödə", url=odeniş_url),
        types.InlineKeyboardButton("🔄 Ödənişi yoxla", callback_data=f"yoxla_{order_ref}"),
        types.InlineKeyboardButton("❌ Ləğv et", callback_data="legv"),
    )
    metin = (
        "💳 <b>Onlayn ödəniş</b>\n"
        "Aşağıdakı düymədən kart, SBP və ya kriptovalyuta ilə ödəniş edə bilərsiniz.\n"
        "Ödəniş edən kimi sifariş <b>avtomatik</b> göndəriləcək — heç nə göndərməyinizə ehtiyac yoxdur ✅\n\n"
        f"🔖 Sifariş kodu: <code>{order_ref}</code>"
    )
    bot.send_message(call.message.chat.id, metin, reply_markup=kb)


def odenis_novu_manual_basla(call, order_ref):
    """İstifadəçi 'Bank kartına köçürmə' seçəndə çağırılır — kart məlumatı göstərilir."""
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    if not row:
        conn.close()
        bot.answer_callback_query(call.id, "Sifariş tapılmadı", show_alert=True)
        return
    if row["status"] != "ODENIS_GOZLENILIR":
        conn.close()
        bot.answer_callback_query(call.id, "Bu sifariş artıq emal olunub.", show_alert=True)
        return
    conn.execute("UPDATE sifarisler SET odenis_novu='manual' WHERE order_ref=?", (order_ref,))
    conn.commit()
    conn.close()

    bot.answer_callback_query(call.id)

    user_states[call.from_user.id] = {"stage": "manual_qebz_gozlenilir", "order_ref": order_ref}

    kart = manual_odenis_melumati_al()
    metin = (
        f"🏦 <b>Manual ödəniş</b>\n\n"
        f"Bank: <b>{kart['bank_adi']}</b>\n"
        f"Kart nömrəsi: <code>{kart['kart_nomresi']}</code>\n"
        f"Kart sahibi: <b>{kart['kart_sahibi']}</b>\n\n"
        f"💵 Məbləğ: {row['satis_qiymeti']:.2f} {PUL_VAHIDI}\n"
        f"🔖 Sifariş kodu: <code>{order_ref}</code>\n\n"
        "Köçürməni etdikdən sonra qəbzin (kvitansiyanın) şəklini bu bota göndərin. "
        "Admin yoxlayıb təsdiqlədikdən sonra sifarişiniz avtomatik göndəriləcək ✅"
    )
    bot.send_message(call.message.chat.id, metin)


# ----------------------------------------------------------------
# TELEGRAM: MANUAL ÖDƏNİŞ QƏBZİNİN (ŞƏKİL) QƏBULU
# ----------------------------------------------------------------
@bot.message_handler(
    content_types=["photo"],
    func=lambda m: user_states.get(m.from_user.id, {}).get("stage") == "manual_qebz_gozlenilir",
)
def manual_qebz_qebul_et(message):
    user_id = message.from_user.id
    state = user_states.get(user_id, {})
    order_ref = state.get("order_ref")

    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    if not row:
        conn.close()
        bot.send_message(user_id, "❌ Sifariş tapılmadı, /start ilə yenidən başlayın.")
        return

    file_id = message.photo[-1].file_id
    conn.execute("UPDATE sifarisler SET manual_qebz_file_id=? WHERE order_ref=?", (file_id, order_ref))
    conn.commit()
    conn.close()

    user_states.pop(user_id, None)

    bot.send_message(user_id, "✅ Qəbziniz qəbul edildi. Admin yoxlayan kimi sifarişiniz göndəriləcək ⏳")

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"manual_tesdiq_{order_ref}"),
        types.InlineKeyboardButton("❌ Rədd et", callback_data=f"manual_redd_{order_ref}"),
    )
    bot.send_photo(
        ADMIN_ID,
        file_id,
        caption=(
            f"🏦 Manual ödəniş qəbzi\n\n"
            f"👤 İstifadəçi: {row['username'] or row['user_id']}\n"
            f"📦 {row['product_ad']}\n"
            f"🆔 ID: {row['player_id']}\n"
            f"💵 Məbləğ: {row['satis_qiymeti']:.2f} {PUL_VAHIDI}\n"
            f"🔖 Sifariş: {order_ref}"
        ),
        reply_markup=kb,
    )


# ----------------------------------------------------------------
# TELEGRAM: CALLBACK (DÜYMƏ) İDARƏEDİCİSİ
# ----------------------------------------------------------------
@bot.callback_query_handler(func=lambda c: True)
def callback_isleyici(call):
    try:
        if call.data == "menu_uc":
            bot.edit_message_text(
                "💎 Zəhmət olmasa UC paketini seçin:",
                call.message.chat.id, call.message.message_id, reply_markup=mehsul_menyusu(),
            )

        elif call.data == "menu_basla":
            bot.edit_message_text(
                "🎮 <b>PUBG MOBILE UC YÜKLƏMƏ</b> ⚡\n\nAşağıdakı düymələrdən birini seçin 👇",
                call.message.chat.id, call.message.message_id, reply_markup=basla_menyusu(),
            )

        elif call.data == "menu_balans":
            balansim_goster(call)

        elif call.data.startswith("sec_"):
            mehsul_sec(call, call.data[4:])

        elif call.data.startswith("yoxla_"):
            odenis_yoxla(call, call.data[len("yoxla_"):])

        elif call.data.startswith("odenis_enot_"):
            odenis_novu_enot_basla(call, call.data[len("odenis_enot_"):])

        elif call.data.startswith("odenis_manual_"):
            odenis_novu_manual_basla(call, call.data[len("odenis_manual_"):])

        elif call.data.startswith("manual_tesdiq_"):
            manual_odenisi_tesdiqle(call, call.data[len("manual_tesdiq_"):])

        elif call.data.startswith("manual_redd_"):
            manual_odenisi_redd_et(call, call.data[len("manual_redd_"):])

        elif call.data == "legv":
            user_states.pop(call.from_user.id, None)
            bot.edit_message_text("❌ Əməliyyat ləğv edildi.", call.message.chat.id, call.message.message_id)

        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Callback xətası: {e}")
        try:
            bot.answer_callback_query(call.id, "Xəta baş verdi, yenidən cəhd edin.")
        except Exception:
            pass


def manual_odenisi_tesdiqle(call, order_ref):
    """Admin manual ödənişi təsdiqləyəndə çağırılır — sifariş EpinBulk-a göndərilir."""
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔️ Bu əməliyyat yalnız admin üçündür.", show_alert=True)
        return

    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    conn.close()
    if not row:
        bot.answer_callback_query(call.id, "Sifariş tapılmadı", show_alert=True)
        return
    if row["status"] != "ODENIS_GOZLENILIR":
        bot.answer_callback_query(call.id, "Bu sifariş artıq emal olunub.", show_alert=True)
        return

    bot.answer_callback_query(call.id, "✅ Təsdiqləndi, sifariş göndərilir...")
    try:
        bot.edit_message_caption(
            caption=call.message.caption + "\n\n✅ TƏSDİQLƏNDİ",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )
    except Exception:
        pass

    threading.Thread(target=odenis_ugurlu_emal_et, args=(order_ref,), daemon=True).start()


def manual_odenisi_redd_et(call, order_ref):
    """Admin manual ödənişi (saxta/səhv qəbz və s.) rədd edəndə çağırılır."""
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔️ Bu əməliyyat yalnız admin üçündür.", show_alert=True)
        return

    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    if not row:
        conn.close()
        bot.answer_callback_query(call.id, "Sifariş tapılmadı", show_alert=True)
        return
    if row["status"] != "ODENIS_GOZLENILIR":
        conn.close()
        bot.answer_callback_query(call.id, "Bu sifariş artıq emal olunub.", show_alert=True)
        return

    conn.execute("UPDATE sifarisler SET status='XETA', netice='Manual ödəniş admin tərəfindən rədd edildi' WHERE order_ref=?", (order_ref,))
    conn.commit()
    conn.close()

    bot.answer_callback_query(call.id, "❌ Rədd edildi.")
    try:
        bot.edit_message_caption(
            caption=call.message.caption + "\n\n❌ RƏDD EDİLDİ",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )
    except Exception:
        pass

    bot.send_message(
        row["user_id"],
        "❌ Göndərdiyiniz qəbz təsdiqlənmədi. Zəhmət olmasa admin ilə əlaqə saxlayın.",
    )


def mehsul_sec(call, key):
    product = products.get(key)
    if not product:
        bot.answer_callback_query(call.id, "Məhsul tapılmadı")
        return

    user_states[call.from_user.id] = {"stage": "id_gozlenilir", "product_key": key}

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("❌ Ləğv et", callback_data="legv"))

    bot.edit_message_text(
        f"📦 Seçdiyiniz paket: <b>{product['ad']}</b>\n"
        f"💵 Qiymət: {product['qiymet']:.2f} {PUL_VAHIDI}\n\n"
        "🆔 Zəhmət olmasa PUBG Mobile ID nömrənizi göndərin (yalnız rəqəm):",
        call.message.chat.id, call.message.message_id, reply_markup=kb,
    )


def odenis_yoxla(call, order_ref):
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    conn.close()

    if not row:
        bot.answer_callback_query(call.id, "Sifariş tapılmadı", show_alert=True)
        return

    if row["status"] == "ODENIS_GOZLENILIR":
        bot.answer_callback_query(call.id, "⏳ Ödəniş hələ təsdiqlənməyib.", show_alert=True)
    elif row["status"] in ("ODENILDI", "EPINBULK_GOZLENILIR"):
        bot.answer_callback_query(call.id, "⏳ Sifarişiniz icra olunur, bir az gözləyin.", show_alert=True)
    elif row["status"] == "TAMAMLANDI":
        bot.answer_callback_query(call.id, "✅ Sifariş artıq tamamlanıb.", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "❌ Sifarişdə xəta olub, admin ilə əlaqə saxlayın.", show_alert=True)


def balansim_goster(call):
    user_id = call.from_user.id
    conn = db_baglan()
    sonuncular = conn.execute(
        "SELECT * FROM sifarisler WHERE user_id=? ORDER BY id DESC LIMIT 5", (user_id,)
    ).fetchall()
    cemi = conn.execute(
        "SELECT COALESCE(SUM(satis_qiymeti),0) c FROM sifarisler WHERE user_id=? AND status='TAMAMLANDI'",
        (user_id,),
    ).fetchone()["c"]
    conn.close()

    metin = f"💼 <b>Hesabatım</b>\n\n💰 Ümumi xərclədiyiniz: {cemi:.2f} {PUL_VAHIDI}\n\n"
    if sonuncular:
        metin += "🧾 Son sifarişləriniz:\n"
        for r in sonuncular:
            metin += f"• {r['product_ad']} — {vaziyyet_emoji(r['status'])} {vaziyyet_metni(r['status'])}\n"
    else:
        metin += "Hələ heç bir sifarişiniz yoxdur."

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="menu_basla"))
    bot.edit_message_text(metin, call.message.chat.id, call.message.message_id, reply_markup=kb)


# ----------------------------------------------------------------
# FON DÖVRƏLƏRİ (background thread-lər)
# ----------------------------------------------------------------
_balans_xeberdarligi_gonderilib = False


def balans_izleme_dovresi():
    """30 dəqiqədə bir EpinBulk balansını yoxlayır, azalanda admin-ə xəbər verir."""
    global _balans_xeberdarligi_gonderilib
    while True:
        try:
            balans = epinbulk_balans()
            if balans is not None:
                if balans < MIN_BALANCE_ALERT and not _balans_xeberdarligi_gonderilib:
                    bot.send_message(ADMIN_ID, f"⚠️ Balans azdır! Cari balans: {balans:.2f} USD")
                    _balans_xeberdarligi_gonderilib = True
                elif balans >= MIN_BALANCE_ALERT:
                    _balans_xeberdarligi_gonderilib = False
        except Exception as e:
            logger.error(f"Balans yoxlanışı dövrəsində xəta: {e}")
        time.sleep(1800)


def gozleyen_sifarisleri_yoxlama_dovresi():
    """20 saniyədə bir EPINBULK_GOZLENILIR statuslu sifarişləri yoxlayır."""
    while True:
        try:
            conn = db_baglan()
            rows = conn.execute("SELECT * FROM sifarisler WHERE status='EPINBULK_GOZLENILIR'").fetchall()
            conn.close()
            for row in rows:
                gozleyen_sifarisi_yoxla(row)
        except Exception as e:
            logger.error(f"Gözləyən sifarişlər yoxlanarkən xəta: {e}")
        time.sleep(20)


def flask_islet():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


def bot_polling_dovresi():
    """Bot çökərsə 10 saniyə sonra özü yenidən başlayır."""
    while True:
        try:
            logger.info("Bot polling başladı ✅")
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Bot çökdü: {e}")
        logger.info("10 saniyə sonra bot yenidən başladılacaq...")
        time.sleep(10)


# ----------------------------------------------------------------
# ƏSAS BAŞLANĞIC NÖQTƏSİ
# ----------------------------------------------------------------
if __name__ == "__main__":
    if "--mehsullari-tap" in sys.argv:
        mehsullari_tap_ve_cap_et()
        sys.exit(0)

    init_db()
    threading.Thread(target=flask_islet, daemon=True).start()
    threading.Thread(target=balans_izleme_dovresi, daemon=True).start()
    threading.Thread(target=gozleyen_sifarisleri_yoxlama_dovresi, daemon=True).start()
    bot_polling_dovresi()
