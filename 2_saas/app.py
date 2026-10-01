import json
import os
import sqlite3

import requests
from flask import Flask, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient
from paypalserversdk.configuration import Environment
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.models.create_subscription_request import CreateSubscriptionRequest
from paypalserversdk.models.subscription_application_context import (
    SubscriptionApplicationContext,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET"]

CLIENT_ID = os.environ["PAYPAL_CLIENT_ID"]
CLIENT_SECRET = os.environ["PAYPAL_CLIENT_SECRET"]
WEBHOOK_ID = os.environ["PAYPAL_WEBHOOK_ID"]
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
PAYPAL_API = "https://api-m.sandbox.paypal.com"

paypal = PaypalServersdkClient(
    client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
        o_auth_client_id=CLIENT_ID,
        o_auth_client_secret=CLIENT_SECRET,
    ),
    environment=Environment.SANDBOX,
)

PLANS = {
    "basic": {"name": "Basic", "price": "9.99", "plan_id": os.environ.get("PLAN_ID_BASIC")},
    "pro": {"name": "Pro", "price": "29.99", "plan_id": os.environ.get("PLAN_ID_PRO")},
}

DB_PATH = "saas.db"


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
            tier TEXT DEFAULT 'free',
            subscription_id TEXT,
            sub_status TEXT
        );
        CREATE TABLE IF NOT EXISTS webhook_events (
            id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL,
            resource_id TEXT,
            received_at TEXT DEFAULT CURRENT_TIMESTAMP
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
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (request.form["email"], generate_password_hash(request.form["password"])),
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
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid credentials.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/")
def index():
    return render_template("index.html", plans=PLANS, user=current_user())


@app.route("/dashboard")
def dashboard():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    return render_template("dashboard.html", user=user)


@app.route("/premium")
def premium():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    if user["tier"] == "free":
        return render_template("upgrade_required.html", user=user)
    return render_template("premium.html", user=user)


@app.route("/subscribe/<tier>")
def subscribe(tier):
    user = current_user()
    if user is None:
        return redirect(url_for("login"))

    subscription = paypal.subscriptions.create_subscription({
        "body": CreateSubscriptionRequest(
            plan_id=PLANS[tier]["plan_id"],
            custom_id=str(user["id"]),
            application_context=SubscriptionApplicationContext(
                brand_name="Demo SaaS",
                return_url=f"{BASE_URL}/subscribe/success",
                cancel_url=f"{BASE_URL}/dashboard",
                user_action="SUBSCRIBE_NOW",
            ),
        ),
        "prefer": "return=representation",
    })

    for link in subscription.body.links:
        if link.rel == "approve":
            return redirect(link.href)
    return "Could not start subscription", 500


@app.route("/subscribe/success")
def subscribe_success():
    return render_template("success.html", user=current_user())


@app.route("/cancel")
def cancel():
    user = current_user()
    if user is None or not user["subscription_id"]:
        return redirect(url_for("dashboard"))
    paypal.subscriptions.cancel_subscription({
        "id": user["subscription_id"],
        "body": {"reason": "Cancelled by user"},
    })
    return redirect(url_for("dashboard"))


def get_access_token():
    res = requests.post(
        f"{PAYPAL_API}/v1/oauth2/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    res.raise_for_status()
    return res.json()["access_token"]


def verify_webhook(req):
    res = requests.post(
        f"{PAYPAL_API}/v1/notifications/verify-webhook-signature",
        headers={"Authorization": f"Bearer {get_access_token()}"},
        json={
            "transmission_id": req.headers.get("paypal-transmission-id"),
            "transmission_time": req.headers.get("paypal-transmission-time"),
            "cert_url": req.headers.get("paypal-cert-url"),
            "auth_algo": req.headers.get("paypal-auth-algo"),
            "transmission_sig": req.headers.get("paypal-transmission-sig"),
            "webhook_id": WEBHOOK_ID,
            "webhook_event": req.get_json(),
        },
        timeout=10,
    )
    return res.json().get("verification_status") == "SUCCESS"


@app.route("/webhook", methods=["POST"])
def webhook():
    if not verify_webhook(request):
        return "Invalid signature", 400

    event = request.get_json()
    event_type = event["event_type"]
    resource = event.get("resource", {})

    db = get_db()
    db.execute(
        "INSERT INTO webhook_events (event_type, resource_id) VALUES (?, ?)",
        (event_type, resource.get("id")),
    )

    user_id = resource.get("custom_id")
    sub_id = resource.get("id")

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        plan_id = resource.get("plan_id")
        tier = next((k for k, v in PLANS.items() if v["plan_id"] == plan_id), "free")
        db.execute(
            "UPDATE users SET tier = ?, subscription_id = ?, sub_status = ? WHERE id = ?",
            (tier, sub_id, "ACTIVE", user_id),
        )

    elif event_type in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.EXPIRED"):
        db.execute(
            "UPDATE users SET tier = 'free', sub_status = ? WHERE subscription_id = ?",
            ("CANCELLED", sub_id),
        )

    elif event_type == "BILLING.SUBSCRIPTION.SUSPENDED":
        db.execute(
            "UPDATE users SET tier = 'free', sub_status = ? WHERE subscription_id = ?",
            ("SUSPENDED", sub_id),
        )

    elif event_type == "PAYMENT.SALE.COMPLETED":
        sale_sub_id = resource.get("billing_agreement_id")
        if user_id:
            db.execute(
                "UPDATE users SET subscription_id = ?, sub_status = ? WHERE id = ?",
                (sale_sub_id, "ACTIVE", user_id),
            )
        else:
            db.execute(
                "UPDATE users SET sub_status = ? WHERE subscription_id = ?",
                ("ACTIVE", sale_sub_id),
            )

    db.commit()
    return "", 204


@app.route("/events")
def events():
    rows = get_db().execute(
        "SELECT * FROM webhook_events ORDER BY id DESC LIMIT 25"
    ).fetchall()
    return render_template("events.html", events=rows, user=current_user())


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5001)
