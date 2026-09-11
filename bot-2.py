# ================================================================
# BossEpin Bot — PUBG Mobile UC Satış Botu (@bossepin_az_bot)
# ----------------------------------------------------------------
# Tam işlək Telegram Epin satış botu:
#   - EpinBulk API (https://epinbulk.shop/docs) ilə UC sifarişi
#   - MANUAL KART ÖDƏNİŞİ (kart-kart): botda kart nömrəsi və "kart
#     sahibi" adı göstərilir, müştəri çeki (skrinşotu) göndərir,
#     admin/işçi qrupda təsdiqləyir
#   - Admin paneldən: yeni məhsul əlavə etmək, qiymət/EpinBulk ID
#     dəyişmək, kart məlumatlarını dəyişmək, admin/işçi əlavə etmək
#   - Flask "keep-alive" server (UptimeRobot üçün)
#   - Çökmə zamanı 10 saniyə sonra avtomatik yenidən başlama
#   - Bütün xətalar log.txt-ə yazılır
#
# QEYD (VACİB): EpinBulk-un rəsmi sənədinə görə autentifikasiya
# "Bearer Token" ilə DEYİL, "X-API-KEY" HTTP header-i ilədir.
# Kodda düzgün üsul istifadə olunub (bax: epinbulk_basliq()).
#
# ÖDƏNİŞ AXINI (bu versiyada Yığım.az API-si YOXDUR, tam manualdır):
#   1) Müştəri məhsul seçir, oyunçu ID-sini göndərir
#   2) Bot kart nömrəsini və "kart sahibi" adını göstərir
#   3) Müştəri pulu köçürür, çekin şəklini bota göndərir (və ya
#      /tamam yazaraq ödədiyini bildirir)
#   4) Çek/bildiriş avtomatik təyin olunmuş QRUPA düşür, orada
#      "✅ Təsdiqlə" / "❌ Rədd et" düymələri olur
#   5) Admin və ya işçi (ödəniş təsdiqi üçün əlavə edilmiş şəxs)
#      düyməyə basanda sifariş EpinBulk-a göndərilir və müştəriyə
#      avtomatik çatdırılır
#
# Lazımi kitabxanalar:
#   pip install pyTelegramBotAPI requests flask
# ================================================================

import os
import sys
import re
import uuid
import time
import sqlite3
import logging
import threading
from datetime import datetime

import requests
from flask import Flask
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
TELEGRAM_TOKEN = "8574808806:AAGSk0UE-n29M-Y3biKDYoXDwsnaXxSU3g8"
EPINBULK_API_KEY = "EPINBULK_KEY"

# Botu ilk dəfə işə salan, həmişə "admin" olan Telegram istifadəçi ID-si.
# Bundan başqa admin/işçiləri botun içindən (admin panel) əlavə edə bilərsiniz.
ADMIN_ID = 7262941693

EPINBULK_BASE_URL = "https://epinbulk.shop/api/v1"

PUL_VAHIDI = "AZN"          # Satış qiymətlərinizin valyutası (lazım olsa dəyişin)
MIN_BALANCE_ALERT = 5.0     # USD — EpinBulk balansı bundan az olanda admin xəbərdar edilir

DB_PATH = "bossepin.db"

# Kart məlumatlarının başlanğıc (default) dəyərləri — bunları admin
# paneldən ("💳 Kart məlumatı") istənilən vaxt dəyişmək mümkündür.
DEFAULT_KART_NOMRESI = "0000 0000 0000 0000"
DEFAULT_KART_ADI = "Ad Soyad"   # Qeyd: bura real ad qoymaq şərt deyil

# ----------------------------------------------------------------
# BAŞLANĞIC MƏHSUL SİYAHISI (yalnız ilk dəfə DB yaradılanda əlavə olunur)
# Sonradan admin paneldən yeni məhsul əlavə etmək / silmək / dəyişmək olar.
# epinbulk_id sahələri boşdur. Doldurmaq üçün:
#   1) EPINBULK_API_KEY-i yuxarıda doldurun
#   2) terminalda: python3 bot.py --mehsullari-tap
#   3) çıxan siyahıdan uyğun ID-ləri admin paneldən məhsula yazın
# ----------------------------------------------------------------
BASLANGIC_MEHSULLAR = [
    ("60_uc", "60 UC", 2.00),
    ("180_uc", "180 UC", 5.00),
    ("325_uc", "325 UC", 8.50),
    ("385_uc", "385 UC", 10.50),
    ("660_uc", "660 UC", 16.00),
    ("720_uc", "720 UC", 18.00),
    ("780_uc", "780 UC", 20.00),
    ("985_uc", "985 UC", 24.00),
    ("1320_uc", "1320 UC", 32.00),
    ("1800_uc", "1800 UC", 39.50),
    ("1980_uc", "1980 UC", 45.00),
    ("2125_uc", "2125 UC", 48.00),
    ("2460_uc", "2460 UC", 56.00),
    ("3120_uc", "3120 UC", 71.00),
    ("3850_uc", "3850 UC", 80.00),
    ("4030_uc", "4030 UC", 85.00),
    ("5170_uc", "5170 UC", 113.00),
    ("5650_uc", "5650 UC", 119.00),
    ("8100_uc", "8100 UC", 155.00),
    ("9900_uc", "9900 UC", 194.00),
    ("11950_uc", "11950 UC", 235.00),
    ("16200_uc", "16200 UC", 309.00),
]

# ----------------------------------------------------------------
# SİFARİŞ STATUSLARININ İZAHI
# ----------------------------------------------------------------
VEZIYYET_METNLERI = {
    "ODENIS_GOZLENILIR": "Ödəniş gözlənilir",
    "ODEME_BILDIRILDI": "Ödəniş bildirildi (təsdiq gözlənilir)",
    "ODENILDI": "Təsdiqləndi, icra olunur",
    "EPINBULK_GOZLENILIR": "İcra olunur",
    "TAMAMLANDI": "Tamamlandı",
    "RED_EDILDI": "Rədd edildi",
    "XETA": "Xəta",
}
VEZIYYET_EMOJILERI = {
    "ODENIS_GOZLENILIR": "⏳",
    "ODEME_BILDIRILDI": "📩",
    "ODENILDI": "⏳",
    "EPINBULK_GOZLENILIR": "⏳",
    "TAMAMLANDI": "✅",
    "RED_EDILDI": "❌",
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
            cek_file_id TEXT,
            onaylayan_id INTEGER,
            qrup_mesaj_id INTEGER,
            epinbulk_order_id TEXT,
            epinbulk_qiymet REAL,
            netice TEXT,
            yaradilma_tarixi TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mehsullar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            ad TEXT,
            qiymet REAL,
            epinbulk_id TEXT DEFAULT '',
            aktiv INTEGER DEFAULT 1,
            sira INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS adminler (
            user_id INTEGER PRIMARY KEY,
            ad TEXT,
            rol TEXT,
            elave_edilib TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ayarlar (
            acar TEXT PRIMARY KEY,
            deyer TEXT
        )
    """)

    # Başlanğıc məhsulları yalnız cədvəl boşdursa əlavə et
    sayi = conn.execute("SELECT COUNT(*) c FROM mehsullar").fetchone()[0]
    if sayi == 0:
        for i, (key, ad, qiymet) in enumerate(BASLANGIC_MEHSULLAR):
            conn.execute(
                "INSERT INTO mehsullar (key, ad, qiymet, epinbulk_id, aktiv, sira) VALUES (?,?,?,?,1,?)",
                (key, ad, qiymet, "", i),
            )

    # Bootstrap admin — həmişə admin qalır, panel içindən silinə bilməz
    conn.execute(
        "INSERT OR IGNORE INTO adminler (user_id, ad, rol, elave_edilib) VALUES (?,?,?,?)",
        (ADMIN_ID, "Əsas Admin", "admin", datetime.now().isoformat()),
    )

    # Kart məlumatlarının default dəyərləri (əgər hələ ayarlanmayıbsa)
    conn.execute("INSERT OR IGNORE INTO ayarlar (acar, deyer) VALUES ('kart_nomresi', ?)", (DEFAULT_KART_NOMRESI,))
    conn.execute("INSERT OR IGNORE INTO ayarlar (acar, deyer) VALUES ('kart_adi', ?)", (DEFAULT_KART_ADI,))
    conn.execute("INSERT OR IGNORE INTO ayarlar (acar, deyer) VALUES ('qrup_id', '')")

    conn.commit()
    conn.close()


def db_baglan():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ----------------------------------------------------------------
# AYARLAR (kart nömrəsi, kart adı, qrup id) — DB-də saxlanılır
# ----------------------------------------------------------------
def ayar_al(acar, default=""):
    conn = db_baglan()
    row = conn.execute("SELECT deyer FROM ayarlar WHERE acar=?", (acar,)).fetchone()
    conn.close()
    return row["deyer"] if row and row["deyer"] is not None else default


def ayar_yaz(acar, deyer):
    conn = db_baglan()
    conn.execute(
        "INSERT INTO ayarlar (acar, deyer) VALUES (?,?) ON CONFLICT(acar) DO UPDATE SET deyer=excluded.deyer",
        (acar, deyer),
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------
# MƏHSULLAR — DB-dən oxuma/yazma
# ----------------------------------------------------------------
def mehsullari_al(aktiv_only=True):
    conn = db_baglan()
    if aktiv_only:
        rows = conn.execute("SELECT * FROM mehsullar WHERE aktiv=1 ORDER BY sira, id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM mehsullar ORDER BY sira, id").fetchall()
    conn.close()
    return rows


def mehsul_al(key):
    conn = db_baglan()
    row = conn.execute("SELECT * FROM mehsullar WHERE key=?", (key,)).fetchone()
    conn.close()
    return row


def mehsul_acari_yarat(ad):
    """Məhsul adından unikal DB açarı (key) yaradır, məsələn '660 UC' -> '660_uc'."""
    esas = re.sub(r"[^a-z0-9]+", "_", ad.strip().lower()).strip("_") or "mehsul"
    conn = db_baglan()
    key = esas
    sayaç = 1
    while conn.execute("SELECT 1 FROM mehsullar WHERE key=?", (key,)).fetchone():
        sayaç += 1
        key = f"{esas}_{sayaç}"
    conn.close()
    return key


# ----------------------------------------------------------------
# ADMİN / İŞÇİ ROLLARI
# ----------------------------------------------------------------
def rol_al(user_id):
    if user_id == ADMIN_ID:
        return "admin"
    conn = db_baglan()
    row = conn.execute("SELECT rol FROM adminler WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["rol"] if row else None


def admin_dir(user_id):
    return rol_al(user_id) == "admin"


def isci_dir(user_id):
    """Admin da, ödəniş təsdiqi üçün əlavə olunan işçi də daxildir."""
    return rol_al(user_id) in ("admin", "isci")


def adminleri_al():
    conn = db_baglan()
    rows = conn.execute("SELECT * FROM adminler ORDER BY rol, ad").fetchall()
    conn.close()
    return rows


# ----------------------------------------------------------------
# TELEGRAM BOT VƏ FLASK
# ----------------------------------------------------------------
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)

user_states = {}  # {telegram_user_id: {"stage": "...", ...}}


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
# SİFARİŞİN EMALI (EpinBulk-a göndərmə və müştəriyə çatdırma)
# ----------------------------------------------------------------
def sifaris_teslim_et(user_id, sifaris_id, data):
    """EpinBulk sifarişi COMPLETED olanda müştəriyə nəticəni göndərir."""
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


def odenisi_epinbulka_gonder(order_ref):
    """Ödəniş admin/işçi tərəfindən təsdiqləndikdən sonra çağırılır:
    sifarişi EpinBulk-a göndərir və nəticəyə görə müştəriyə bildiriş verir."""
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref = ?", (order_ref,)).fetchone()
    conn.close()
    if not row:
        logger.error(f"Naməlum order_ref üçün təsdiq gəldi: {order_ref}")
        return

    product = mehsul_al(row["product_key"])
    epinbulk_id = product["epinbulk_id"] if product else None

    if not epinbulk_id:
        logger.error(f"'{row['product_key']}' üçün epinbulk_id doldurulmayıb!")
        bot.send_message(row["user_id"], "❌ Texniki xəta baş verdi. Admin sizinlə əlaqə saxlayacaq.")
        qrupa_mesaj_gonder(f"‼️ '{row['product_key']}' üçün EpinBulk ID boşdur — sifariş #{row['id']} icra oluna bilmədi.")
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
        qrupa_mesaj_gonder(f"‼️ Sifariş #{row['id']} EpinBulk xətası: {xeta}")
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
        bot.send_message(row["user_id"], "✅ Ödənişiniz təsdiqləndi! Sifarişiniz icra olunur ⏳")


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
        qrupa_mesaj_gonder(f"‼️ Sifariş #{row['id']} {status} oldu.")


# ----------------------------------------------------------------
# QRUPA BİLDİRİŞ GÖNDƏRMƏ (ödəniş bildirişləri buraya düşür)
# ----------------------------------------------------------------
def qrup_id_al():
    qid = ayar_al("qrup_id", "")
    return int(qid) if qid else None


def tesdiq_duymeleri(order_ref):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"onayla_{order_ref}"),
        types.InlineKeyboardButton("❌ Rədd et", callback_data=f"redd_{order_ref}"),
    )
    return kb


def qrupa_mesaj_gonder(metin, reply_markup=None):
    """Ümumi mətn bildirişini qrupa (yoxdursa admin-ə) göndərir."""
    qid = qrup_id_al()
    hedef = qid if qid else ADMIN_ID
    try:
        return bot.send_message(hedef, metin, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Qrupa mesaj göndərilə bilmədi: {e}")
        return None


def yeni_odenis_bildirisi_gonder(row):
    """Yeni ödəniş bildirişi (çeksiz, /tamam ilə) qrupa göndərilir."""
    metin = (
        "🆕 <b>Yeni ödəniş bildirişi!</b>\n\n"
        f"🧾 Sifariş: #{row['id']}\n"
        f"👤 İstifadəçi: @{row['username'] or '—'} (ID: {row['user_id']})\n"
        f"📦 Məhsul: {row['product_ad']}\n"
        f"🎮 Oyunçu ID: {row['player_id']}\n"
        f"💵 Məbləğ: {row['satis_qiymeti']:.2f} {PUL_VAHIDI}\n\n"
        "Müştəri hələ çek göndərməyib — çek gələndə bura əlavə ediləcək.\n"
        "Ödənişi bank hesabınızdan yoxladıqdan sonra təsdiqləyin 👇"
    )
    msg = qrupa_mesaj_gonder(metin, reply_markup=tesdiq_duymeleri(row["order_ref"]))
    if msg:
        conn = db_baglan()
        conn.execute("UPDATE sifarisler SET qrup_mesaj_id=? WHERE order_ref=?", (msg.message_id, row["order_ref"]))
        conn.commit()
        conn.close()


def cek_bildirisi_gonder(row, file_id):
    """Müştərinin göndərdiyi çekin şəklini qrupa göndərir."""
    izah = (
        "🧾 <b>Ödəniş çeki gəldi!</b>\n\n"
        f"Sifariş: #{row['id']}\n"
        f"👤 İstifadəçi: @{row['username'] or '—'} (ID: {row['user_id']})\n"
        f"📦 Məhsul: {row['product_ad']}\n"
        f"🎮 Oyunçu ID: {row['player_id']}\n"
        f"💵 Məbləğ: {row['satis_qiymeti']:.2f} {PUL_VAHIDI}\n\n"
        "Çeki yoxlayıb təsdiqləyin və ya rədd edin 👇"
    )
    qid = qrup_id_al()
    hedef = qid if qid else ADMIN_ID
    try:
        msg = bot.send_photo(hedef, file_id, caption=izah, reply_markup=tesdiq_duymeleri(row["order_ref"]))
        conn = db_baglan()
        conn.execute("UPDATE sifarisler SET qrup_mesaj_id=? WHERE order_ref=?", (msg.message_id, row["order_ref"]))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Çek qrupa göndərilə bilmədi: {e}")


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
        types.InlineKeyboardButton(f"{p['ad']} - {p['qiymet']:.2f} {PUL_VAHIDI}", callback_data=f"sec_{p['key']}")
        for p in mehsullari_al(aktiv_only=True)
    ]
    if duymeler:
        kb.add(*duymeler)
    kb.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="menu_basla"))
    return kb


# ----------------------------------------------------------------
# TELEGRAM: ƏMRLƏR (adi istifadəçilər)
# ----------------------------------------------------------------
@bot.message_handler(commands=["start"])
def start_emri(message):
    user_states.pop(message.from_user.id, None)
    metin = (
        "🎮 <b>PUBG MOBILE UC YÜKLƏMƏ</b> ⚡\n\n"
        "Ən sürətli və etibarlı UC yükləmə botuna xoş gəlmisiniz!\n"
        "Aşağıdakı düymələrdən birini seçin 👇"
    )
    bot.send_message(message.chat.id, metin, reply_markup=basla_menyusu())


@bot.message_handler(commands=["tamam"])
def tamam_emri(message):
    """Müştəri ödənişi etdiyini /tamam yazaraq bildirir (çek göndərməsə belə)."""
    user_id = message.from_user.id
    conn = db_baglan()
    row = conn.execute(
        "SELECT * FROM sifarisler WHERE user_id=? AND status='ODENIS_GOZLENILIR' ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()

    if not row:
        bot.send_message(user_id, "ℹ️ Hazırda gözlənilən (ödənişi bildirilməmiş) sifarişiniz yoxdur.")
        return

    _odemeni_bildir(row)


def _odemeni_bildir(row):
    conn = db_baglan()
    conn.execute("UPDATE sifarisler SET status='ODEME_BILDIRILDI' WHERE order_ref=?", (row["order_ref"],))
    conn.commit()
    conn.close()

    yeni_odenis_bildirisi_gonder(row)
    bot.send_message(
        row["user_id"],
        "✅ Bildirişiniz göndərildi. Zəhmət olmasa çekin (ödəniş skrinşotunun) şəklini də bura göndərin ki, "
        "admin daha tez təsdiqləsin.\nAdmin təsdiqləyən kimi sifarişiniz avtomatik icra olunacaq.",
    )


# ----------------------------------------------------------------
# TELEGRAM: ADMİN PANEL
# ----------------------------------------------------------------
def admin_ana_menyu(user_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🕓 Gözləyən ödənişlər", callback_data="adm_gozleyen"))
    kb.add(types.InlineKeyboardButton("📊 Statistika", callback_data="adm_stat"))
    if admin_dir(user_id):
        kb.add(types.InlineKeyboardButton("📦 Məhsullar", callback_data="adm_mehsullar"))
        kb.add(types.InlineKeyboardButton("💳 Kart məlumatı", callback_data="adm_kart"))
        kb.add(types.InlineKeyboardButton("👥 Admin/İşçilər", callback_data="adm_isciler"))
    return kb


@bot.message_handler(commands=["admin"])
def admin_paneli(message):
    if not isci_dir(message.from_user.id):
        bot.send_message(message.chat.id, "⛔️ Bu əmrə icazəniz yoxdur.")
        return
    bot.send_message(message.chat.id, "👑 <b>Admin Panel</b>\n\nBölmə seçin 👇", reply_markup=admin_ana_menyu(message.from_user.id))


@bot.message_handler(commands=["qrupu_tayin"])
def qrupu_tayin(message):
    """Bu əmr, bot əlavə olunmuş QRUPUN içində admin tərəfindən yazılmalıdır.
    Ödəniş bildirişləri/çeklər bundan sonra həmin qrupa düşəcək."""
    if not admin_dir(message.from_user.id):
        return
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "ℹ️ Bu əmri ödəniş bildirişlərinin düşməli olduğu QRUPUN içində yazın.")
        return
    ayar_yaz("qrup_id", str(message.chat.id))
    bot.reply_to(message, "✅ Bu qrup ödəniş bildirişləri üçün təyin olundu.")


def admin_statistika_metni():
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
        "📊 <b>Statistika</b>\n\n"
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
    return metin, balans


def gozleyen_odenisler_goster(chat_id, message_id=None):
    conn = db_baglan()
    rows = conn.execute(
        "SELECT * FROM sifarisler WHERE status IN ('ODENIS_GOZLENILIR','ODEME_BILDIRILDI') ORDER BY id DESC LIMIT 15"
    ).fetchall()
    conn.close()

    kb = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        metin = "🕓 <b>Gözləyən ödənişlər</b>\n\nHazırda gözləyən ödəniş yoxdur."
    else:
        metin = "🕓 <b>Gözləyən ödənişlər</b>\n\nTəsdiqləmək üçün sifariş seçin 👇"
        for r in rows:
            etiket = f"#{r['id']} {r['product_ad']} — {r['satis_qiymeti']:.2f} {PUL_VAHIDI} {vaziyyet_emoji(r['status'])}"
            kb.add(types.InlineKeyboardButton(etiket, callback_data=f"bax_{r['order_ref']}"))
    kb.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="adm_geri"))

    if message_id:
        bot.edit_message_text(metin, chat_id, message_id, reply_markup=kb)
    else:
        bot.send_message(chat_id, metin, reply_markup=kb)


def sifaris_detali_goster(call, order_ref):
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    conn.close()
    if not row:
        bot.answer_callback_query(call.id, "Sifariş tapılmadı", show_alert=True)
        return

    metin = (
        f"🧾 Sifariş #{row['id']}\n"
        f"👤 @{row['username'] or '—'} (ID: {row['user_id']})\n"
        f"📦 {row['product_ad']}\n"
        f"🎮 Oyunçu ID: {row['player_id']}\n"
        f"💵 {row['satis_qiymeti']:.2f} {PUL_VAHIDI}\n"
        f"Status: {vaziyyet_emoji(row['status'])} {vaziyyet_metni(row['status'])}"
    )
    kb = types.InlineKeyboardMarkup()
    if row["status"] in ("ODENIS_GOZLENILIR", "ODEME_BILDIRILDI"):
        kb.add(
            types.InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"onayla_{order_ref}"),
            types.InlineKeyboardButton("❌ Rədd et", callback_data=f"redd_{order_ref}"),
        )
    kb.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="adm_gozleyen"))

    if row["cek_file_id"]:
        bot.send_photo(call.message.chat.id, row["cek_file_id"], caption=metin, reply_markup=kb)
    else:
        bot.edit_message_text(metin, call.message.chat.id, call.message.message_id, reply_markup=kb)


# ---- Məhsullar bölməsi ------------------------------------------------
def mehsullar_menyusu_goster(chat_id, message_id=None):
    rows = mehsullari_al(aktiv_only=False)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for p in rows:
        vez = "✅" if p["aktiv"] else "⛔️"
        id_vez = "🆔" if p["epinbulk_id"] else "⚠️ID yox"
        etiket = f"{vez} {p['ad']} — {p['qiymet']:.2f} {PUL_VAHIDI} ({id_vez})"
        kb.add(types.InlineKeyboardButton(etiket, callback_data=f"mprod_{p['key']}"))
    kb.add(types.InlineKeyboardButton("➕ Yeni məhsul", callback_data="myeni"))
    kb.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="adm_geri"))

    metin = "📦 <b>Məhsullar</b>\n\nDəyişmək üçün məhsul seçin, ya da yeni əlavə edin 👇"
    if message_id:
        bot.edit_message_text(metin, chat_id, message_id, reply_markup=kb)
    else:
        bot.send_message(chat_id, metin, reply_markup=kb)


def mehsul_detali_goster(chat_id, key, message_id=None):
    p = mehsul_al(key)
    if not p:
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("💰 Qiyməti dəyiş", callback_data=f"mqiymet_{key}"),
        types.InlineKeyboardButton("🆔 EpinBulk ID-ni dəyiş", callback_data=f"mid_{key}"),
        types.InlineKeyboardButton("⛔️ Deaktiv et" if p["aktiv"] else "✅ Aktiv et", callback_data=f"maktiv_{key}"),
        types.InlineKeyboardButton("🗑 Sil", callback_data=f"msil_{key}"),
        types.InlineKeyboardButton("⬅️ Geri", callback_data="adm_mehsullar"),
    )
    metin = (
        f"📦 <b>{p['ad']}</b>\n"
        f"💰 Qiymət: {p['qiymet']:.2f} {PUL_VAHIDI}\n"
        f"🆔 EpinBulk ID: {p['epinbulk_id'] or '— (təyin olunmayıb)'}\n"
        f"Status: {'✅ Aktiv' if p['aktiv'] else '⛔️ Deaktiv'}"
    )
    if message_id:
        bot.edit_message_text(metin, chat_id, message_id, reply_markup=kb)
    else:
        bot.send_message(chat_id, metin, reply_markup=kb)


# ---- Kart məlumatı bölməsi ---------------------------------------------
def kart_menyusu_goster(chat_id, message_id=None):
    kart_no = ayar_al("kart_nomresi", DEFAULT_KART_NOMRESI)
    kart_ad = ayar_al("kart_adi", DEFAULT_KART_ADI)
    metin = (
        "💳 <b>Kart məlumatı</b>\n\n"
        f"Kart nömrəsi: <code>{kart_no}</code>\n"
        f"Kart sahibinin adı: {kart_ad}\n\n"
        "Bu məlumatlar müştərilərə ödəniş zamanı göstərilir."
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("✏️ Kart nömrəsini dəyiş", callback_data="kkart_no"),
        types.InlineKeyboardButton("✏️ Kart sahibinin adını dəyiş", callback_data="kkart_ad"),
        types.InlineKeyboardButton("⬅️ Geri", callback_data="adm_geri"),
    )
    if message_id:
        bot.edit_message_text(metin, chat_id, message_id, reply_markup=kb)
    else:
        bot.send_message(chat_id, metin, reply_markup=kb)


# ---- Admin/İşçi idarəetməsi --------------------------------------------
def isciler_menyusu_goster(chat_id, message_id=None):
    rows = adminleri_al()
    metin = "👥 <b>Admin / İşçilər</b>\n\n"
    for r in rows:
        rol_ad = "👑 Admin" if r["rol"] == "admin" else "🧑‍💼 İşçi (ödəniş təsdiqi)"
        metin += f"• {r['ad'] or r['user_id']} — {rol_ad} (ID: {r['user_id']})\n"

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("➕ İşçi əlavə et (yalnız ödəniş təsdiqi)", callback_data="iyeni_isci"),
        types.InlineKeyboardButton("➕ Admin əlavə et (tam icazə)", callback_data="iyeni_admin"),
        types.InlineKeyboardButton("🗑 Sil", callback_data="isil_menyu"),
        types.InlineKeyboardButton("⬅️ Geri", callback_data="adm_geri"),
    )
    if message_id:
        bot.edit_message_text(metin, chat_id, message_id, reply_markup=kb)
    else:
        bot.send_message(chat_id, metin, reply_markup=kb)


def silme_menyusu_goster(call):
    rows = [r for r in adminleri_al() if r["user_id"] != ADMIN_ID]
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        bot.answer_callback_query(call.id, "Silinəcək admin/işçi yoxdur (əsas admin silinə bilməz).", show_alert=True)
        return
    for r in rows:
        kb.add(types.InlineKeyboardButton(f"🗑 {r['ad'] or r['user_id']}", callback_data=f"isil_{r['user_id']}"))
    kb.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="adm_isciler"))
    bot.edit_message_text("Silmək istədiyiniz şəxsi seçin:", call.message.chat.id, call.message.message_id, reply_markup=kb)


# ----------------------------------------------------------------
# TELEGRAM: sadə mətn cavabı gözləyən əməliyyatlar (register_next_step_handler)
# ----------------------------------------------------------------
def _legv_edilebilen(message, geri_callback):
    """İstifadəçi mətn əvəzinə /legv yazsa əməliyyatı ləğv edir."""
    return message.text and message.text.strip() == "/legv"


def kart_no_deyis_addim(message):
    if _legv_edilebilen(message, None):
        bot.send_message(message.chat.id, "Ləğv edildi."); return
    ayar_yaz("kart_nomresi", message.text.strip())
    bot.send_message(message.chat.id, "✅ Kart nömrəsi yeniləndi.")
    kart_menyusu_goster(message.chat.id)


def kart_ad_deyis_addim(message):
    if _legv_edilebilen(message, None):
        bot.send_message(message.chat.id, "Ləğv edildi."); return
    ayar_yaz("kart_adi", message.text.strip())
    bot.send_message(message.chat.id, "✅ Kart sahibinin adı yeniləndi.")
    kart_menyusu_goster(message.chat.id)


def mehsul_qiymet_deyis_addim(message, key):
    try:
        yeni_qiymet = float(message.text.strip().replace(",", "."))
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ Zəhmət olmasa yalnız rəqəm göndərin (məs: 12.50).")
        return
    conn = db_baglan()
    conn.execute("UPDATE mehsullar SET qiymet=? WHERE key=?", (yeni_qiymet, key))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "✅ Qiymət yeniləndi.")
    mehsul_detali_goster(message.chat.id, key)


def mehsul_id_deyis_addim(message, key):
    yeni_id = message.text.strip()
    conn = db_baglan()
    conn.execute("UPDATE mehsullar SET epinbulk_id=? WHERE key=?", (yeni_id, key))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "✅ EpinBulk ID yeniləndi.")
    mehsul_detali_goster(message.chat.id, key)


def yeni_mehsul_ad_addim(message):
    ad = message.text.strip()
    bot.send_message(message.chat.id, "💰 Qiyməti daxil edin (məs: 12.50):")
    bot.register_next_step_handler(message, yeni_mehsul_qiymet_addim, ad)


def yeni_mehsul_qiymet_addim(message, ad):
    try:
        qiymet = float(message.text.strip().replace(",", "."))
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ Rəqəm gözlənilirdi. Yenidən /admin yazıb başlayın.")
        return
    bot.send_message(message.chat.id, "🆔 EpinBulk məhsul ID-sini daxil edin (bilmirsinizsə '-' yazın):")
    bot.register_next_step_handler(message, yeni_mehsul_id_addim, ad, qiymet)


def yeni_mehsul_id_addim(message, ad, qiymet):
    epinbulk_id = message.text.strip()
    if epinbulk_id == "-":
        epinbulk_id = ""
    key = mehsul_acari_yarat(ad)
    conn = db_baglan()
    sira = conn.execute("SELECT COALESCE(MAX(sira),0)+1 s FROM mehsullar").fetchone()["s"]
    conn.execute(
        "INSERT INTO mehsullar (key, ad, qiymet, epinbulk_id, aktiv, sira) VALUES (?,?,?,?,1,?)",
        (key, ad, qiymet, epinbulk_id, sira),
    )
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, f"✅ Yeni məhsul əlavə olundu: {ad}")
    mehsullar_menyusu_goster(message.chat.id)


def isci_id_qebul_addim(message, rol):
    hedef_id = None
    hedef_ad = ""
    if message.forward_from:
        hedef_id = message.forward_from.id
        hedef_ad = message.forward_from.full_name
    elif message.text and message.text.strip().isdigit():
        hedef_id = int(message.text.strip())
        hedef_ad = f"ID {hedef_id}"
    else:
        bot.send_message(message.chat.id, "⚠️ Ya həmin şəxsin mesajını forward edin, ya da Telegram ID-sini (rəqəm) göndərin.")
        return

    conn = db_baglan()
    conn.execute(
        "INSERT INTO adminler (user_id, ad, rol, elave_edilib) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET rol=excluded.rol, ad=excluded.ad",
        (hedef_id, hedef_ad, rol, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    rol_metni = "admin" if rol == "admin" else "işçi (ödəniş təsdiqi)"
    bot.send_message(message.chat.id, f"✅ {hedef_ad} indi {rol_metni} kimi əlavə olundu.")
    isciler_menyusu_goster(message.chat.id)


# ----------------------------------------------------------------
# TELEGRAM: CALLBACK (DÜYMƏ) İDARƏEDİCİSİ
# ----------------------------------------------------------------
@bot.callback_query_handler(func=lambda c: True)
def callback_isleyici(call):
    try:
        data = call.data
        user_id = call.from_user.id

        # ---- İstifadəçi menyusu ----
        if data == "menu_uc":
            bot.edit_message_text(
                "💎 Zəhmət olmasa UC paketini seçin:",
                call.message.chat.id, call.message.message_id, reply_markup=mehsul_menyusu(),
            )
        elif data == "menu_basla":
            bot.edit_message_text(
                "🎮 <b>PUBG MOBILE UC YÜKLƏMƏ</b> ⚡\n\nAşağıdakı düymələrdən birini seçin 👇",
                call.message.chat.id, call.message.message_id, reply_markup=basla_menyusu(),
            )
        elif data == "menu_balans":
            balansim_goster(call)
        elif data.startswith("sec_"):
            mehsul_sec(call, data[4:])
        elif data.startswith("tamam_"):
            _tamam_callback(call, data[len("tamam_"):])
        elif data == "legv":
            user_states.pop(user_id, None)
            bot.edit_message_text("❌ Əməliyyat ləğv edildi.", call.message.chat.id, call.message.message_id)

        # ---- Ödəniş təsdiqi (qrupda / admin paneldə) ----
        elif data.startswith("onayla_"):
            _odenisi_onayla(call, data[len("onayla_"):])
        elif data.startswith("redd_"):
            _odenisi_redd_et(call, data[len("redd_"):])
        elif data.startswith("bax_"):
            sifaris_detali_goster(call, data[len("bax_"):])

        # ---- Admin panel naviqasiyası ----
        elif data == "adm_geri":
            bot.edit_message_text("👑 <b>Admin Panel</b>\n\nBölmə seçin 👇", call.message.chat.id, call.message.message_id, reply_markup=admin_ana_menyu(user_id))
        elif data == "adm_gozleyen":
            gozleyen_odenisler_goster(call.message.chat.id, call.message.message_id)
        elif data == "adm_stat":
            if isci_dir(user_id):
                metin, _ = admin_statistika_metni()
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="adm_geri"))
                bot.edit_message_text(metin, call.message.chat.id, call.message.message_id, reply_markup=kb)
        elif data == "adm_mehsullar" and admin_dir(user_id):
            mehsullar_menyusu_goster(call.message.chat.id, call.message.message_id)
        elif data == "adm_kart" and admin_dir(user_id):
            kart_menyusu_goster(call.message.chat.id, call.message.message_id)
        elif data == "adm_isciler" and admin_dir(user_id):
            isciler_menyusu_goster(call.message.chat.id, call.message.message_id)

        # ---- Məhsul idarəetməsi ----
        elif data.startswith("mprod_") and admin_dir(user_id):
            mehsul_detali_goster(call.message.chat.id, data[len("mprod_"):], call.message.message_id)
        elif data.startswith("mqiymet_") and admin_dir(user_id):
            key = data[len("mqiymet_"):]
            msg = bot.send_message(call.message.chat.id, "💰 Yeni qiyməti daxil edin (məs: 12.50):")
            bot.register_next_step_handler(msg, mehsul_qiymet_deyis_addim, key)
        elif data.startswith("mid_") and admin_dir(user_id):
            key = data[len("mid_"):]
            msg = bot.send_message(call.message.chat.id, "🆔 Yeni EpinBulk məhsul ID-sini daxil edin:")
            bot.register_next_step_handler(msg, mehsul_id_deyis_addim, key)
        elif data.startswith("maktiv_") and admin_dir(user_id):
            key = data[len("maktiv_"):]
            conn = db_baglan()
            conn.execute("UPDATE mehsullar SET aktiv = 1 - aktiv WHERE key=?", (key,))
            conn.commit()
            conn.close()
            mehsul_detali_goster(call.message.chat.id, key, call.message.message_id)
        elif data.startswith("msil_") and admin_dir(user_id):
            key = data[len("msil_"):]
            conn = db_baglan()
            conn.execute("DELETE FROM mehsullar WHERE key=?", (key,))
            conn.commit()
            conn.close()
            mehsullar_menyusu_goster(call.message.chat.id, call.message.message_id)
        elif data == "myeni" and admin_dir(user_id):
            msg = bot.send_message(call.message.chat.id, "📦 Yeni məhsulun adını daxil edin (məs: 660 UC):")
            bot.register_next_step_handler(msg, yeni_mehsul_ad_addim)

        # ---- Kart məlumatı ----
        elif data == "kkart_no" and admin_dir(user_id):
            msg = bot.send_message(call.message.chat.id, "💳 Yeni kart nömrəsini daxil edin:")
            bot.register_next_step_handler(msg, kart_no_deyis_addim)
        elif data == "kkart_ad" and admin_dir(user_id):
            msg = bot.send_message(call.message.chat.id, "✏️ Kart sahibi üçün göstəriləcək adı daxil edin:")
            bot.register_next_step_handler(msg, kart_ad_deyis_addim)

        # ---- Admin/İşçi idarəetməsi ----
        elif data == "iyeni_isci" and admin_dir(user_id):
            msg = bot.send_message(call.message.chat.id, "🧑‍💼 Yeni işçinin mesajını forward edin, ya da Telegram ID-sini (rəqəm) göndərin:")
            bot.register_next_step_handler(msg, isci_id_qebul_addim, "isci")
        elif data == "iyeni_admin" and admin_dir(user_id):
            msg = bot.send_message(call.message.chat.id, "👑 Yeni adminin mesajını forward edin, ya da Telegram ID-sini (rəqəm) göndərin:")
            bot.register_next_step_handler(msg, isci_id_qebul_addim, "admin")
        elif data == "isil_menyu" and admin_dir(user_id):
            silme_menyusu_goster(call)
        elif data.startswith("isil_") and admin_dir(user_id):
            hedef_id = int(data[len("isil_"):])
            if hedef_id == ADMIN_ID:
                bot.answer_callback_query(call.id, "Əsas admin silinə bilməz.", show_alert=True)
            else:
                conn = db_baglan()
                conn.execute("DELETE FROM adminler WHERE user_id=?", (hedef_id,))
                conn.commit()
                conn.close()
                isciler_menyusu_goster(call.message.chat.id, call.message.message_id)

        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Callback xətası: {e}")
        try:
            bot.answer_callback_query(call.id, "Xəta baş verdi, yenidən cəhd edin.")
        except Exception:
            pass


def mehsul_sec(call, key):
    product = mehsul_al(key)
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


def _tamam_callback(call, order_ref):
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    conn.close()
    if not row:
        bot.answer_callback_query(call.id, "Sifariş tapılmadı", show_alert=True)
        return
    if row["status"] != "ODENIS_GOZLENILIR":
        bot.answer_callback_query(call.id, "Bu sifariş üçün bildiriş artıq göndərilib.", show_alert=True)
        return
    _odemeni_bildir(row)
    bot.answer_callback_query(call.id, "✅ Bildiriş göndərildi.")


def _icaze_var_mi(call):
    if not isci_dir(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔️ Bu əməliyyat üçün icazəniz yoxdur.", show_alert=True)
        return False
    return True


def _odenisi_onayla(call, order_ref):
    if not _icaze_var_mi(call):
        return
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    conn.close()
    if not row:
        bot.answer_callback_query(call.id, "Sifariş tapılmadı", show_alert=True)
        return
    if row["status"] not in ("ODENIS_GOZLENILIR", "ODEME_BILDIRILDI"):
        bot.answer_callback_query(call.id, "Bu sifariş artıq emal olunub.", show_alert=True)
        return

    conn = db_baglan()
    conn.execute("UPDATE sifarisler SET status='ODENILDI', onaylayan_id=? WHERE order_ref=?", (call.from_user.id, order_ref))
    conn.commit()
    conn.close()

    try:
        yeni_metin = (call.message.caption or call.message.text or "") + f"\n\n✅ Təsdiqləndi — @{call.from_user.username or call.from_user.id}"
        if call.message.photo:
            bot.edit_message_caption(yeni_metin, call.message.chat.id, call.message.message_id)
        else:
            bot.edit_message_text(yeni_metin, call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    bot.answer_callback_query(call.id, "✅ Ödəniş təsdiqləndi, sifariş icra olunur.")
    threading.Thread(target=odenisi_epinbulka_gonder, args=(order_ref,), daemon=True).start()


def _odenisi_redd_et(call, order_ref):
    if not _icaze_var_mi(call):
        return
    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    if not row or row["status"] not in ("ODENIS_GOZLENILIR", "ODEME_BILDIRILDI"):
        conn.close()
        bot.answer_callback_query(call.id, "Bu sifariş üçün əməliyyat mümkün deyil.", show_alert=True)
        return
    conn.execute("UPDATE sifarisler SET status='RED_EDILDI', onaylayan_id=? WHERE order_ref=?", (call.from_user.id, order_ref))
    conn.commit()
    conn.close()

    try:
        yeni_metin = (call.message.caption or call.message.text or "") + f"\n\n❌ Rədd edildi — @{call.from_user.username or call.from_user.id}"
        if call.message.photo:
            bot.edit_message_caption(yeni_metin, call.message.chat.id, call.message.message_id)
        else:
            bot.edit_message_text(yeni_metin, call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    bot.send_message(row["user_id"], "❌ Ödənişiniz təsdiqlənmədi. Zəhmət olmasa admin ilə əlaqə saxlayın.")
    bot.answer_callback_query(call.id, "❌ Rədd edildi.")


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
# TELEGRAM: ID NÖMRƏSİNİN QƏBULU VƏ SİFARİŞİN YARADILMASI
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
        product = mehsul_al(key)
        if not product:
            bot.send_message(user_id, "❌ Məhsul artıq mövcud deyil, yenidən /start yazın.")
            user_states.pop(user_id, None)
            return
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

        kart_no = ayar_al("kart_nomresi", DEFAULT_KART_NOMRESI)
        kart_ad = ayar_al("kart_adi", DEFAULT_KART_ADI)

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ Ödədim", callback_data=f"tamam_{order_ref}"))
        kb.add(types.InlineKeyboardButton("❌ Ləğv et", callback_data="legv"))

        bot.send_message(
            user_id,
            f"📦 {product['ad']}\n🆔 ID: {id_metni}\n💵 Məbləğ: {product['qiymet']:.2f} {PUL_VAHIDI}\n\n"
            "💳 <b>Ödəniş məlumatları</b>\n"
            f"Kart nömrəsi: <code>{kart_no}</code>\n"
            f"Kart sahibi: {kart_ad}\n\n"
            "Ödənişi etdikdən sonra çekin (ödəniş skrinşotunun) şəklini birbaşa bura göndərin.\n"
            "Şəkli göndərə bilmirsinizsə, aşağıdakı \"✅ Ödədim\" düyməsinə basın və ya /tamam yazın.",
            reply_markup=kb,
        )
        user_states[user_id] = {"stage": "odeme_gozlenilir", "order_ref": order_ref}
    except Exception as e:
        logger.error(f"ID qəbulunda xəta: {e}")
        bot.send_message(user_id, "❌ Xəta baş verdi, yenidən cəhd edin.")


@bot.message_handler(
    content_types=["photo"],
    func=lambda m: user_states.get(m.from_user.id, {}).get("stage") in ("odeme_gozlenilir", "tesdiq_gozlenilir"),
)
def cek_qebul_et(message):
    user_id = message.from_user.id
    order_ref = user_states.get(user_id, {}).get("order_ref")
    if not order_ref:
        return

    conn = db_baglan()
    row = conn.execute("SELECT * FROM sifarisler WHERE order_ref=?", (order_ref,)).fetchone()
    if not row or row["status"] not in ("ODENIS_GOZLENILIR", "ODEME_BILDIRILDI"):
        conn.close()
        return

    file_id = message.photo[-1].file_id
    conn.execute(
        "UPDATE sifarisler SET cek_file_id=?, status='ODEME_BILDIRILDI' WHERE order_ref=?",
        (file_id, order_ref),
    )
    conn.commit()
    conn.close()

    row = dict(row)
    row["cek_file_id"] = file_id
    cek_bildirisi_gonder(row, file_id)

    bot.send_message(
        user_id,
        "✅ Çekiniz qəbul edildi! Admin ödənişi yoxlayıb təsdiqlədikdən sonra sifarişiniz avtomatik icra olunacaq. "
        "Bir az səbrli olun 🙏",
    )
    user_states[user_id] = {"stage": "tesdiq_gozlenilir", "order_ref": order_ref}


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
        init_db()
        mehsullari_tap_ve_cap_et()
        sys.exit(0)

    init_db()
    threading.Thread(target=flask_islet, daemon=True).start()
    threading.Thread(target=balans_izleme_dovresi, daemon=True).start()
    threading.Thread(target=gozleyen_sifarisleri_yoxlama_dovresi, daemon=True).start()
    bot_polling_dovresi()
