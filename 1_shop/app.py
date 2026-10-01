import os
import sqlite3

from flask import Flask, g, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient
from paypalserversdk.configuration import Environment
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.models.order_request import OrderRequest
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET"]

app.config["PAYPAL_CLIENT_ID"] = os.environ["PAYPAL_CLIENT_ID"]

paypal = PaypalServersdkClient(
    client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
        o_auth_client_id=os.environ["PAYPAL_CLIENT_ID"],
        o_auth_client_secret=os.environ["PAYPAL_CLIENT_SECRET"],
    ),
    environment=Environment.SANDBOX,
)

PRODUCTS = {
    1: {"name": "Python Hoodie", "price": "49.99"},
    2: {"name": "Mechanical Keyboard", "price": "89.99"},
    3: {"name": "Coffee Mug", "price": "14.99"},
}

DB_PATH = "shop.db"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            paypal_order_id TEXT NOT NULL,
            amount TEXT NOT NULL,
            status TEXT NOT NULL,
            items TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.commit()
    db.close()


def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]
        pw_hash = generate_password_hash(request.form["password"])
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, pw_hash),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Email already registered.")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = get_db().execute(
            "SELECT * FROM users WHERE email = ?", (request.form["email"],)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/")
def index():
    return render_template(
        "index.html", products=PRODUCTS, user=current_user(), cart=session.get("cart", {})
    )


@app.route("/cart/add/<int:product_id>")
def add_to_cart(product_id):
    cart = session.get("cart", {})
    cart[str(product_id)] = cart.get(str(product_id), 0) + 1
    session["cart"] = cart
    return redirect(url_for("index"))


@app.route("/cart/clear")
def clear_cart():
    session.pop("cart", None)
    return redirect(url_for("index"))


def cart_total(cart):
    total = sum(float(PRODUCTS[int(pid)]["price"]) * qty for pid, qty in cart.items())
    return f"{total:.2f}"


@app.route("/api/orders", methods=["POST"])
def create_order():
    if current_user() is None:
        return jsonify({"error": "login required"}), 401

    cart = session.get("cart", {})
    if not cart:
        return jsonify({"error": "cart is empty"}), 400

    order = paypal.orders.create_order({
        "body": OrderRequest(
            intent=CheckoutPaymentIntent.CAPTURE,
            purchase_units=[
                PurchaseUnitRequest(
                    amount=AmountWithBreakdown(
                        currency_code="USD",
                        value=cart_total(cart),
                    )
                )
            ],
        )
    })
    order_id = order.body.id
    session[f"order_{order_id}"] = {"cart": cart, "total": cart_total(cart)}
    return jsonify({"id": order_id})


@app.route("/api/orders/<order_id>/capture", methods=["POST"])
def capture_order(order_id):
    user = current_user()
    if user is None:
        return jsonify({"error": "login required"}), 401

    pending = session.get(f"order_{order_id}")
    if pending is None:
        return jsonify({"error": "unknown order"}), 400

    result = paypal.orders.capture_order({"id": order_id, "prefer": "return=representation"})
    captured = result.body

    if captured.status == "COMPLETED":
        paid = captured.purchase_units[0].payments.captures[0].amount.value
        if float(paid) != float(pending["total"]):
            return jsonify({"error": "amount mismatch"}), 400

        cart = pending["cart"]
        items = ", ".join(
            f"{PRODUCTS[int(pid)]['name']} x{qty}" for pid, qty in cart.items()
        )
        db = get_db()
        db.execute(
            "INSERT INTO orders (user_id, paypal_order_id, amount, status, items)"
            " VALUES (?, ?, ?, ?, ?)",
            (user["id"], captured.id, paid, "PAID", items),
        )
        db.commit()
        session.pop("cart", None)
        session.pop(f"order_{order_id}", None)

    return jsonify({"status": captured.status})


@app.route("/orders")
def my_orders():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    orders = get_db().execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", (user["id"],)
    ).fetchall()
    return render_template("orders.html", orders=orders, user=user)


@app.route("/admin")
def admin():
    user = current_user()
    if user is None or not user["is_admin"]:
        return "Forbidden", 403
    orders = get_db().execute(
        "SELECT o.*, u.email FROM orders o JOIN users u ON u.id = o.user_id"
        " ORDER BY o.id DESC"
    ).fetchall()
    return render_template("admin.html", orders=orders, user=user)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
