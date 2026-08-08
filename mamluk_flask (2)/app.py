import functools
import hashlib
import json
import os
import re
import secrets
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from flask import (
    Flask, abort, g, jsonify, redirect, render_template, request,
    send_from_directory, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from db import DB_PATH, SETTINGS_DEFAULTS, get_db, init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_ASSETS_DIR = os.path.join(BASE_DIR, "root_assets")
IS_PRODUCTION = os.environ.get("MAMLUK_ENV") == "production"

app = Flask(__name__)
# In production, set MAMLUK_SECRET_KEY via an environment variable instead of
# regenerating it on every restart (that would invalidate all sessions/admins).
app.secret_key = os.environ.get("MAMLUK_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Cookies marked "Secure" are dropped by browsers over plain HTTP, so this is
# only turned on in production (where MAMLUK_ENV=production implies HTTPS).
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
# Caps request body size (form uploads, etc.) to blunt simple DoS attempts.
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB

SHIPPING_COST = 500
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
DISCOUNT_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,30}$")

init_db()


# ------------------------------------------------------------ rate limiting --
# Simple in-memory sliding-window limiter: fine for a single-process demo /
# small store. For real production traffic behind multiple workers, replace
# this with a shared store (e.g. Redis + Flask-Limiter).
_rate_buckets = defaultdict(list)


def rate_limit(max_calls, per_seconds, key_prefix):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            bucket_key = f"{key_prefix}:{request.remote_addr}"
            now = time.time()
            hits = _rate_buckets[bucket_key]
            hits[:] = [t for t in hits if now - t < per_seconds]
            if len(hits) >= max_calls:
                message = "طلبات كثيرة جدًا، حاول بعد قليل." if is_ar() else "Too many requests, try again shortly."
                return message, 429
            hits.append(now)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------- helpers --

def is_ar():
    return session.get("lang", "ar") == "ar"


def theme():
    return session.get("theme", "red")


def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM site_settings").fetchall()
    conn.close()
    data = dict(SETTINGS_DEFAULTS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def visitor_hash():
    """Anonymized, non-reversible identifier for rough unique-visitor counts.
    We never store the raw IP address."""
    raw = f"{request.remote_addr}|{request.headers.get('User-Agent', '')}|{app.secret_key[:8]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_cart():
    return session.get("cart", [])


def save_cart(cart):
    session["cart"] = cart
    session.modified = True


def cart_with_products():
    """Join the session cart (product_id/size/quantity only) against the
    products table so price always comes from the server, never the client."""
    conn = get_db()
    cart = get_cart()
    items, changed = [], False
    for entry in cart:
        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (entry["product_id"],)
        ).fetchone()
        if not product:
            changed = True
            continue
        items.append({
            "product_id": product["id"],
            "name": product["name_ar"] if is_ar() else product["name_en"],
            "price": product["price"],
            "size": entry["size"],
            "quantity": entry["quantity"],
            "line_total": product["price"] * entry["quantity"],
        })
    conn.close()
    if changed:
        save_cart([{"product_id": i["product_id"], "size": i["size"], "quantity": i["quantity"]} for i in items])
    return items


def cart_count():
    return sum(i["quantity"] for i in get_cart())


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def csrf_valid(token):
    return token and session.get("csrf_token") and secrets.compare_digest(token, session["csrf_token"])


def require_admin():
    return session.get("admin_username") is not None


def get_discount(code):
    """Look up an active, non-expired, not-yet-exhausted discount code."""
    if not code or not DISCOUNT_CODE_RE.match(code):
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM discount_codes WHERE code = ? COLLATE NOCASE AND active = 1", (code,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if row["expires_at"] and datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
        return None
    if row["max_uses"] is not None and row["used_count"] >= row["max_uses"]:
        return None
    return row


def compute_totals(items):
    subtotal = sum(i["line_total"] for i in items)
    discount_row = get_discount(session.get("coupon")) if items else None
    discount_amount = 0
    if discount_row:
        if discount_row["discount_type"] == "percent":
            discount_amount = round(subtotal * discount_row["value"] / 100)
        else:
            discount_amount = discount_row["value"]
        discount_amount = min(discount_amount, subtotal)
    shipping_cost = SHIPPING_COST if items else 0
    total = max(0, subtotal - discount_amount) + shipping_cost
    return {
        "subtotal": subtotal,
        "discount_row": discount_row,
        "discount_amount": discount_amount,
        "shipping_cost": shipping_cost,
        "total": total,
    }


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["is_ar"] = is_ar
app.jinja_env.globals["theme"] = theme
app.jinja_env.globals["settings"] = get_settings
app.jinja_env.globals["cart_count"] = cart_count
app.jinja_env.globals["is_admin"] = require_admin


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # No external scripts/styles/fonts are used anywhere in the site, so we can
    # keep this strict (no 'unsafe-inline', no third-party origins).
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "img-src 'self' data:; style-src 'self'; script-src 'self'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.before_request
def track_visit_and_lang():
    if request.args.get("lang") in ("ar", "en"):
        session["lang"] = request.args.get("lang")
    if request.args.get("theme") in ("red", "black", "white"):
        session["theme"] = request.args.get("theme")

    # Only log real page views, not static assets or admin POST actions.
    if (
        request.method == "GET"
        and not request.path.startswith("/static")
        and request.path not in ("/manifest.json", "/sw.js", "/favicon.ico")
    ):
        conn = get_db()
        conn.execute(
            "INSERT INTO page_visits (path, visitor_hash, user_agent, created_at) VALUES (?,?,?,?)",
            (request.path, visitor_hash(), request.headers.get("User-Agent", "")[:255],
             datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()


# -------------------------------------------------------------- storefront --

@app.route("/")
def index():
    conn = get_db()
    products = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    conn.close()
    toast_message = None
    if request.args.get("added"):
        toast_message = "تمت الإضافة إلى السلة" if is_ar() else "Added to cart"
    return render_template("index.html", products=products, toast_message=toast_message)


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    conn = get_db()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    if not product:
        abort(404)
    toast_message = None
    if request.args.get("added"):
        toast_message = "تمت الإضافة إلى السلة" if is_ar() else "Added to cart"
    return render_template("product_detail.html", p=product, toast_message=toast_message)


@app.route("/shipping")
def shipping():
    return render_template("shipping.html")


@app.route("/contact", methods=["GET", "POST"])
@rate_limit(10, 60, "contact")
def contact():
    error = None
    if request.method == "POST":
        if not csrf_valid(request.form.get("csrf_token")):
            error = "طلب غير صالح، أعد المحاولة." if is_ar() else "Invalid request, please try again."
        else:
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            subject = request.form.get("subject", "").strip()
            message = request.form.get("message", "").strip()

            if not (1 <= len(name) <= 100) or "@" not in email or not (1 <= len(subject) <= 150) \
                    or not (1 <= len(message) <= 2000):
                error = "يرجى التحقق من الحقول المدخلة." if is_ar() else "Please check the fields you entered."
            else:
                conn = get_db()
                conn.execute(
                    "INSERT INTO contact_messages (name, email, phone, subject, message, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (name, email, phone, subject, message, datetime.utcnow().isoformat()),
                )
                conn.commit()
                conn.close()
                return redirect(url_for("contact", sent=1))
    return render_template("contact.html", error=error, sent=request.args.get("sent") == "1")


# -------------------------------------------------------------------- cart --

@app.route("/cart")
def cart_page():
    items = cart_with_products()
    totals = compute_totals(items)
    return render_template("cart.html", items=items, **totals)


@app.route("/cart/add", methods=["POST"])
@rate_limit(30, 60, "cart_add")
def cart_add():
    if not csrf_valid(request.form.get("csrf_token")):
        return redirect(request.referrer or url_for("index"))

    product_id = request.form.get("product_id", type=int)
    size = request.form.get("size", "S")[:10]
    quantity = request.form.get("quantity", type=int) or 1
    quantity = max(1, min(10, quantity))

    conn = get_db()
    product = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    if not product:
        return redirect(url_for("index"))

    cart = get_cart()
    existing = next((e for e in cart if e["product_id"] == product_id and e["size"] == size), None)
    if existing:
        existing["quantity"] = max(1, min(10, existing["quantity"] + quantity))
    else:
        cart.append({"product_id": product_id, "size": size, "quantity": quantity})
    save_cart(cart)

    redirect_to = request.form.get("next") or url_for("index")
    separator = "&" if "?" in redirect_to else "?"
    return redirect(f"{redirect_to}{separator}added={product_id}")


@app.route("/cart/apply-coupon", methods=["POST"])
@rate_limit(20, 60, "apply_coupon")
def cart_apply_coupon():
    if not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("cart_page"))
    code = request.form.get("code", "").strip()
    if get_discount(code):
        session["coupon"] = code
    else:
        session.pop("coupon", None)
        return redirect(url_for("cart_page", coupon_error=1))
    return redirect(url_for("cart_page"))


@app.route("/cart/remove-coupon", methods=["POST"])
def cart_remove_coupon():
    if csrf_valid(request.form.get("csrf_token")):
        session.pop("coupon", None)
    return redirect(url_for("cart_page"))


@app.route("/cart/update", methods=["POST"])
def cart_update():
    if not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("cart_page"))
    product_id = request.form.get("product_id", type=int)
    size = request.form.get("size", "")
    quantity = max(1, min(10, request.form.get("quantity", type=int) or 1))
    cart = get_cart()
    for entry in cart:
        if entry["product_id"] == product_id and entry["size"] == size:
            entry["quantity"] = quantity
    save_cart(cart)
    return redirect(url_for("cart_page"))


@app.route("/cart/remove", methods=["POST"])
def cart_remove():
    if not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("cart_page"))
    product_id = request.form.get("product_id", type=int)
    size = request.form.get("size", "")
    cart = [e for e in get_cart() if not (e["product_id"] == product_id and e["size"] == size)]
    save_cart(cart)
    return redirect(url_for("cart_page"))


# --------------------------------------------------------------- checkout --

@app.route("/checkout", methods=["GET", "POST"])
@rate_limit(10, 60, "checkout")
def checkout():
    items = cart_with_products()
    if not items:
        return redirect(url_for("cart_page"))
    error = None

    if request.method == "POST":
        if not csrf_valid(request.form.get("csrf_token")):
            error = "طلب غير صالح، أعد المحاولة." if is_ar() else "Invalid request, please try again."
        else:
            full_name = request.form.get("fullName", "").strip()
            address = request.form.get("address", "").strip()
            phone = request.form.get("phone", "").strip()
            payment_method = request.form.get("paymentMethod", "cod")

            valid_phone = phone.replace(" ", "").replace("+", "").isdigit() and 9 <= len(phone.replace(" ", "")) <= 15
            if not (1 <= len(full_name) <= 150) or not (1 <= len(address) <= 300) or not valid_phone \
                    or payment_method not in ("cod", "card"):
                error = "يرجى التحقق من بيانات التسليم." if is_ar() else "Please check your delivery details."
            else:
                # Recompute totals server-side one more time at the moment of
                # the order — never trust a total submitted by the client.
                totals = compute_totals(items)
                conn = get_db()
                conn.execute(
                    "INSERT INTO orders (full_name, address, phone, payment_method, subtotal, "
                    "shipping_cost, total, items_json, status, created_at, discount_code, discount_amount) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (full_name, address, phone, payment_method, totals["subtotal"], totals["shipping_cost"],
                     totals["total"], json.dumps(items, ensure_ascii=False), "pending",
                     datetime.utcnow().isoformat(),
                     totals["discount_row"]["code"] if totals["discount_row"] else None,
                     totals["discount_amount"]),
                )
                if totals["discount_row"]:
                    conn.execute(
                        "UPDATE discount_codes SET used_count = used_count + 1 WHERE id = ?",
                        (totals["discount_row"]["id"],),
                    )
                conn.commit()
                conn.close()
                save_cart([])
                session.pop("coupon", None)
                return redirect(url_for("order_success"))

    totals = compute_totals(items)
    return render_template("checkout.html", items=items, error=error, **totals)


@app.route("/order-success")
def order_success():
    return render_template("order_success.html")


# ------------------------------------------------------------------ admin --

@app.route("/admin/login", methods=["GET", "POST"])
@rate_limit(10, 300, "admin_login")
def admin_login():
    if require_admin():
        return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        if not csrf_valid(request.form.get("csrf_token")):
            error = "طلب غير صالح." if is_ar() else "Invalid request."
        else:
            username = request.form.get("username", "").strip()[:120]
            password = request.form.get("password", "")[:200]

            conn = get_db()
            user = conn.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()

            locked = False
            if user and user["locked_until"]:
                locked_until = datetime.fromisoformat(user["locked_until"])
                locked = datetime.utcnow() < locked_until

            if locked:
                error = "تم قفل الحساب مؤقتًا، حاول لاحقًا." if is_ar() else "Account temporarily locked, try later."
            elif user and check_password_hash(user["password_hash"], password):
                conn.execute(
                    "UPDATE admin_users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
                    (user["id"],),
                )
                conn.commit()
                # Keep the shopper's own cart/language, but rotate the CSRF
                # token and drop everything else tied to the pre-login session.
                cart = session.get("cart", [])
                lang = session.get("lang", "ar")
                session.clear()
                session["cart"] = cart
                session["lang"] = lang
                session.permanent = True
                session["admin_username"] = user["username"]
                conn.close()
                return redirect(url_for("admin_dashboard"))
            else:
                if user:
                    attempts = user["failed_attempts"] + 1
                    locked_until = None
                    if attempts >= MAX_LOGIN_ATTEMPTS:
                        locked_until = (datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
                    conn.execute(
                        "UPDATE admin_users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                        (attempts, locked_until, user["id"]),
                    )
                    conn.commit()
                error = "اسم المستخدم أو كلمة المرور غير صحيحة." if is_ar() else "Incorrect username or password."
            conn.close()
    return render_template("admin/login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_username", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin_dashboard():
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db()
    orders = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    total_orders = len(orders)
    revenue = sum(o["total"] for o in orders if o["status"] != "cancelled")
    avg_order = round(revenue / total_orders) if total_orders else 0

    product_counter = Counter()
    for o in orders:
        for it in json.loads(o["items_json"]):
            product_counter[it["name"]] += it["quantity"]
    top_products = product_counter.most_common(5)
    max_top_product = max([c for _, c in top_products], default=1)

    since = (datetime.utcnow() - timedelta(days=13)).date().isoformat()
    order_rows = conn.execute(
        "SELECT substr(created_at,1,10) day, COUNT(*) c, SUM(total) rev FROM orders "
        "WHERE created_at >= ? GROUP BY day ORDER BY day",
        (since,),
    ).fetchall()
    orders_by_day = {r["day"]: {"count": r["c"], "revenue": r["rev"]} for r in order_rows}

    total_visits = conn.execute("SELECT COUNT(*) FROM page_visits").fetchone()[0]
    unique_visitors = conn.execute("SELECT COUNT(DISTINCT visitor_hash) FROM page_visits").fetchone()[0]
    top_pages = conn.execute(
        "SELECT path, COUNT(*) c FROM page_visits GROUP BY path ORDER BY c DESC LIMIT 8"
    ).fetchall()
    max_top_page = max([r["c"] for r in top_pages], default=1)

    visit_rows = conn.execute(
        "SELECT substr(created_at,1,10) day, COUNT(*) c, COUNT(DISTINCT visitor_hash) u "
        "FROM page_visits WHERE created_at >= ? GROUP BY day ORDER BY day",
        (since,),
    ).fetchall()
    visits_by_day = {r["day"]: {"visits": r["c"], "unique": r["u"]} for r in visit_rows}

    days = [(datetime.utcnow().date() - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    timeline = []
    max_visits_day = max([visits_by_day.get(d, {}).get("visits", 0) for d in days], default=1) or 1
    for d in days:
        timeline.append({
            "day": d,
            "visits": visits_by_day.get(d, {}).get("visits", 0),
            "unique": visits_by_day.get(d, {}).get("unique", 0),
            "orders": orders_by_day.get(d, {}).get("count", 0),
            "revenue": orders_by_day.get(d, {}).get("revenue", 0),
        })

    conn.close()
    return render_template(
        "admin/dashboard.html",
        total_orders=total_orders, revenue=revenue, avg_order=avg_order,
        top_products=top_products, max_top_product=max_top_product,
        total_visits=total_visits, unique_visitors=unique_visitors,
        top_pages=top_pages, max_top_page=max_top_page,
        timeline=timeline, max_visits_day=max_visits_day,
        recent_orders=orders[:8],
    )


PRODUCT_VISUAL_CHOICES = ("garment", "garment garment-2", "garment garment-3")
SIZES_RE = re.compile(r"^[A-Za-z0-9]{1,6}$")


def parse_sizes(raw):
    """Turn 'S, M, L , XL' into ['S','M','L','XL'], or None if invalid."""
    tokens = [t.strip().upper() for t in raw.split(",") if t.strip()]
    if not (1 <= len(tokens) <= 8) or not all(SIZES_RE.match(t) for t in tokens):
        return None
    return tokens


@app.route("/admin/products")
def admin_products():
    if not require_admin():
        return redirect(url_for("admin_login"))
    conn = get_db()
    products = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    conn.close()
    return render_template("admin/products.html", products=products,
                            visual_choices=PRODUCT_VISUAL_CHOICES)


@app.route("/admin/products/create", methods=["POST"])
def admin_product_create():
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))

    name_ar = request.form.get("name_ar", "").strip()[:100]
    name_en = request.form.get("name_en", "").strip()[:100]
    description_ar = request.form.get("description_ar", "").strip()[:500]
    description_en = request.form.get("description_en", "").strip()[:500]
    accent_ar = request.form.get("accent_ar", "").strip()[:100]
    accent_en = request.form.get("accent_en", "").strip()[:100]
    price = request.form.get("price", type=int)
    quantity = request.form.get("quantity", type=int)
    visual = request.form.get("visual", PRODUCT_VISUAL_CHOICES[0])
    sizes = parse_sizes(request.form.get("sizes", ""))

    valid = (
        name_ar and name_en and description_ar and description_en and accent_ar and accent_en
        and price is not None and 0 <= price <= 9999999
        and quantity is not None and 0 <= quantity <= 10000
        and visual in PRODUCT_VISUAL_CHOICES
        and sizes is not None
    )
    if valid:
        conn = get_db()
        conn.execute(
            "INSERT INTO products (name_ar, name_en, description_ar, description_en, price, "
            "quantity, sizes, visual, accent_ar, accent_en) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name_ar, name_en, description_ar, description_en, price, quantity,
             ",".join(sizes), visual, accent_ar, accent_en),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_products"))
    return redirect(url_for("admin_products", product_error=1))


@app.route("/admin/products/<int:product_id>/update", methods=["POST"])
def admin_product_update(product_id):
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))

    name_ar = request.form.get("name_ar", "").strip()[:100]
    name_en = request.form.get("name_en", "").strip()[:100]
    description_ar = request.form.get("description_ar", "").strip()[:500]
    description_en = request.form.get("description_en", "").strip()[:500]
    accent_ar = request.form.get("accent_ar", "").strip()[:100]
    accent_en = request.form.get("accent_en", "").strip()[:100]
    price = request.form.get("price", type=int)
    quantity = request.form.get("quantity", type=int)
    visual = request.form.get("visual", PRODUCT_VISUAL_CHOICES[0])
    sizes = parse_sizes(request.form.get("sizes", ""))

    valid = (
        name_ar and name_en and description_ar and description_en and accent_ar and accent_en
        and price is not None and 0 <= price <= 9999999
        and quantity is not None and 0 <= quantity <= 10000
        and visual in PRODUCT_VISUAL_CHOICES
        and sizes is not None
    )
    if valid:
        conn = get_db()
        conn.execute(
            "UPDATE products SET name_ar=?, name_en=?, description_ar=?, description_en=?, "
            "price=?, quantity=?, sizes=?, visual=?, accent_ar=?, accent_en=? WHERE id=?",
            (name_ar, name_en, description_ar, description_en, price, quantity,
             ",".join(sizes), visual, accent_ar, accent_en, product_id),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_products"))
    return redirect(url_for("admin_products", product_error=1))


@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
def admin_product_delete(product_id):
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_products"))


@app.route("/admin/orders")
def admin_orders():
    if not require_admin():
        return redirect(url_for("admin_login"))
    conn = get_db()
    orders = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    conn.close()
    parsed = [{**dict(o), "order_items": json.loads(o["items_json"])} for o in orders]
    return render_template("admin/orders.html", orders=parsed)


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
def admin_order_status(order_id):
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))
    status = request.form.get("status")
    if status in ("pending", "confirmed", "shipped", "delivered", "cancelled"):
        conn = get_db()
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
        conn.commit()
        conn.close()
    return redirect(url_for("admin_orders"))


@app.route("/admin/messages")
def admin_messages():
    if not require_admin():
        return redirect(url_for("admin_login"))
    conn = get_db()
    messages = conn.execute("SELECT * FROM contact_messages ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("admin/messages.html", messages=messages)


@app.route("/admin/change-password", methods=["POST"])
def admin_change_password():
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    username = session["admin_username"]

    if new_password != confirm_password:
        return redirect(url_for("admin_dashboard", pw_error="mismatch"))
    if len(new_password) < 10 or new_password.lower() == username.lower():
        return redirect(url_for("admin_dashboard", pw_error="weak"))

    conn = get_db()
    conn.execute(
        "UPDATE admin_users SET password_hash=? WHERE username=?",
        (generate_password_hash(new_password), username),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard", pw_ok=1))


# --------------------------------------------------------- discount codes --

@app.route("/admin/discounts")
def admin_discounts():
    if not require_admin():
        return redirect(url_for("admin_login"))
    conn = get_db()
    codes = conn.execute("SELECT * FROM discount_codes ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("admin/discounts.html", codes=codes)


@app.route("/admin/discounts/create", methods=["POST"])
def admin_discount_create():
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))

    code = request.form.get("code", "").strip().upper()
    discount_type = request.form.get("discount_type", "percent")
    value = request.form.get("value", type=int)
    max_uses = request.form.get("max_uses", type=int)
    expires_at = request.form.get("expires_at", "").strip()

    error = None
    if not DISCOUNT_CODE_RE.match(code):
        error = "الرمز يجب أن يكون 3-30 حرفًا/رقمًا." if is_ar() else "Code must be 3-30 letters/digits."
    elif discount_type not in ("percent", "fixed"):
        error = "نوع الخصم غير صالح." if is_ar() else "Invalid discount type."
    elif value is None or value <= 0 or (discount_type == "percent" and value > 100) or value > 9999999:
        error = "قيمة الخصم غير صالحة." if is_ar() else "Invalid discount value."
    elif max_uses is not None and max_uses < 0:
        error = "عدد الاستخدامات غير صالح." if is_ar() else "Invalid max uses."

    expires_iso = None
    if expires_at:
        try:
            expires_iso = datetime.fromisoformat(expires_at).isoformat()
        except ValueError:
            error = "تاريخ الانتهاء غير صالح." if is_ar() else "Invalid expiry date."

    if not error:
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO discount_codes (code, discount_type, value, max_uses, expires_at, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (code, discount_type, value, max_uses, expires_iso, datetime.utcnow().isoformat()),
            )
            conn.commit()
        except Exception:
            error = "هذا الرمز مستخدم مسبقًا." if is_ar() else "This code already exists."
        conn.close()

    return redirect(url_for("admin_discounts", error=error) if error else url_for("admin_discounts"))


@app.route("/admin/discounts/<int:discount_id>/toggle", methods=["POST"])
def admin_discount_toggle(discount_id):
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))
    conn = get_db()
    conn.execute(
        "UPDATE discount_codes SET active = 1 - active WHERE id = ?", (discount_id,)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_discounts"))


@app.route("/admin/discounts/<int:discount_id>/delete", methods=["POST"])
def admin_discount_delete(discount_id):
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))
    conn = get_db()
    conn.execute("DELETE FROM discount_codes WHERE id = ?", (discount_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_discounts"))


# ------------------------------------------------------------ site settings --

TEXT_SETTING_KEYS = [
    "hero_eyebrow_ar", "hero_eyebrow_en", "hero_title_ar", "hero_title_en",
    "hero_text_ar", "hero_text_en", "hero_card_title_ar", "hero_card_title_en",
    "hero_card_text_ar", "hero_card_text_en", "support_text_ar", "support_text_en",
    "payment_text_ar", "payment_text_en",
]


@app.route("/admin/settings")
def admin_settings():
    if not require_admin():
        return redirect(url_for("admin_login"))
    return render_template("admin/settings.html", s=get_settings())


@app.route("/admin/settings/update", methods=["POST"])
def admin_settings_update():
    if not require_admin() or not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("admin_login"))

    updates = {}
    for key in TEXT_SETTING_KEYS:
        value = request.form.get(key, "").strip()
        if not (1 <= len(value) <= 600):
            return redirect(url_for("admin_settings", settings_error=1))
        updates[key] = value

    email = request.form.get("contact_email", "").strip()[:150]
    phone_digits = re.sub(r"\D", "", request.form.get("whatsapp_number", ""))
    if "@" not in email or not (8 <= len(phone_digits) <= 15):
        return redirect(url_for("admin_settings", settings_error=1))
    updates["contact_email"] = email
    updates["whatsapp_number"] = phone_digits

    conn = get_db()
    conn.executemany(
        "INSERT INTO site_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        list(updates.items()),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_settings", settings_ok=1))


# ------------------------------------------------------------- root files --

@app.route("/manifest.json")
def manifest():
    return send_from_directory(ROOT_ASSETS_DIR, "manifest.json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory(ROOT_ASSETS_DIR, "sw.js", mimetype="application/javascript")


if __name__ == "__main__":
    # Debug mode (auto-reload + interactive debugger) exposes a remote code
    # execution risk if ever reachable from the internet, so it stays off
    # unless explicitly requested for local development.
    debug_mode = os.environ.get("MAMLUK_DEBUG") == "1"
    # Hosting platforms (Render, Railway, etc.) inject the port to bind via
    # $PORT and expect the app to listen on 0.0.0.0, not just localhost.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", debug=debug_mode, port=port)
