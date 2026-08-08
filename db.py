import os
import sqlite3

from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "mamluk.db")

# Admin login. You can change these later from the dashboard's "Change
# password" form — or override them via environment variables so the real
# password never has to live in the source code.
ADMIN_EMAIL = os.environ.get("MAMLUK_ADMIN_EMAIL", "ylamouri437@gmail.com")
ADMIN_PASSWORD = os.environ.get("MAMLUK_ADMIN_PASSWORD", "ylamouri2007")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name_ar TEXT NOT NULL,
    name_en TEXT NOT NULL,
    description_ar TEXT NOT NULL,
    description_en TEXT NOT NULL,
    price INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    sizes TEXT NOT NULL,
    visual TEXT NOT NULL,
    accent_ar TEXT NOT NULL,
    accent_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    address TEXT NOT NULL,
    phone TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    subtotal INTEGER NOT NULL,
    shipping_cost INTEGER NOT NULL,
    total INTEGER NOT NULL,
    items_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS page_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    visitor_hash TEXT NOT NULL,
    user_agent TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discount_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    discount_type TEXT NOT NULL DEFAULT 'percent',
    value INTEGER NOT NULL,
    max_uses INTEGER,
    used_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_visits_created ON page_visits(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_discount_code ON discount_codes(code);
"""

# Columns added after the initial release. Adding them via ALTER TABLE (and
# ignoring the "duplicate column" error) keeps existing mamluk.db files
# working without the user having to delete their data.
MIGRATIONS = [
    "ALTER TABLE orders ADD COLUMN discount_code TEXT",
    "ALTER TABLE orders ADD COLUMN discount_amount INTEGER NOT NULL DEFAULT 0",
]

# Editable front-end text + public contact info, seeded once then editable
# from /admin/settings. Keys are looked up with these as a fallback so new
# keys added in future versions don't break existing databases.
SETTINGS_DEFAULTS = {
    "hero_eyebrow_ar": "متجر ملابس مملوكي عصري",
    "hero_eyebrow_en": "Modern Mamluk fashion store",
    "hero_title_ar": "أدخل إلى عالم الملابس الحمراء",
    "hero_title_en": "Enter the world of red fashion",
    "hero_text_ar": "تجربة تسوق فاخرة تجمع بين الطابع المملوكي والستايل العصري، مع اختيارات المقاسات والكميات والانتقال السلس إلى تفاصيل اللبس.",
    "hero_text_en": "A luxurious shopping experience blending Mamluk style with contemporary fashion, including size and quantity selection and smooth garment exploration.",
    "hero_card_title_ar": "رداء السلطان",
    "hero_card_title_en": "Sultan's robe",
    "hero_card_text_ar": "مزيج من الأحمر الداكن والذهبي مع لمسة من الفخامة والتفاصيل.",
    "hero_card_text_en": "A blend of deep red and gold with a luxurious touch and fine detailing.",
    "support_text_ar": "يمكنك التواصل معنا عبر واتساب أو البريد الإلكتروني لحل أي مشكلة تخص الطلب أو المقاسات أو الاسترجاع.",
    "support_text_en": "You can reach us via WhatsApp or email for any issue with orders, sizing, or returns.",
    "payment_text_ar": "نقبل الدفع عند الاستلام أو عبر التحويل البنكي، مع إمكانية الدفع الإلكتروني حسب توفر الخدمة.",
    "payment_text_en": "We accept cash on delivery or bank transfer, with digital payment available depending on service.",
    "contact_email": "ylamouri437@gmail.com",
    "whatsapp_number": "213500000000",
}

SEED_PRODUCTS = [
    (
        "رداء السلطان", "Sultan's Robe",
        "قطعة ملكية ذات ألوان حمراء وذهبية وتفاصيل بارزة.",
        "A royal piece with red and gold tones and striking details.",
        32000, 8, "S,M,L,XL", "garment", "رداء القصر", "Palace Robe",
    ),
    (
        "جاكيت المماليك", "Mamluk Jacket",
        "نسخة عصرية مع خطوط بسيطة وتوازن بين الأناقة والراحة.",
        "A modern take with clean lines, balancing elegance and comfort.",
        28000, 5, "M,L,XL", "garment garment-2", "الملمس الفخم", "Luxury Texture",
    ),
    (
        "فستان الواجهة", "Front Dress",
        "أنيق ومميز مع لمسة من السحر الشرقي الحديث.",
        "Elegant and distinctive with a touch of modern oriental charm.",
        41000, 3, "S,M,L", "garment garment-3", "الاختيار الملكي", "Royal Choice",
    ),
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if needed and seed initial data. Safe to call on every startup."""
    conn = get_db()
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    conn.commit()

    for statement in MIGRATIONS:
        try:
            cur.execute(statement)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    if cur.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        cur.executemany(
            """INSERT INTO products
               (name_ar, name_en, description_ar, description_en, price, quantity,
                sizes, visual, accent_ar, accent_en)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            SEED_PRODUCTS,
        )
        conn.commit()

    if cur.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD)),
        )
        conn.commit()

    # Seed any settings keys that don't exist yet — handles both a brand new
    # database and an older database that predates some of these keys.
    existing_keys = {row["key"] for row in cur.execute("SELECT key FROM site_settings").fetchall()}
    missing = [(k, v) for k, v in SETTINGS_DEFAULTS.items() if k not in existing_keys]
    if missing:
        cur.executemany("INSERT INTO site_settings (key, value) VALUES (?, ?)", missing)
        conn.commit()

    conn.close()
