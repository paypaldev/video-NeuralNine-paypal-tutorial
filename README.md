# PayPal Demo Code

This is the prepared code for the NeuralNine YouTube video tutorial, before the AI redesign. It contains only the core functionality, so it's easy to learn from.

Two Flask demos using the PayPal Python SDK (sandbox):

- `1_shop` – web shop with one-time payments
- `2_saas` – SaaS app with subscriptions and webhooks

Requires [uv](https://docs.astral.sh/uv/) and a PayPal developer account.

## 1. Web Shop

```bash
cd 1_shop
cp .env.example .env
```

Fill in `.env`:

- `FLASK_SECRET` – any random string
- `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` – from your sandbox app in the PayPal developer dashboard

Run it:

```bash
uv run app.py
```

Open http://localhost:5000.

## 2. SaaS App

```bash
cd 2_saas
cp .env.example .env
```

Fill in `.env`:

- `FLASK_SECRET`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` – same as above
- `BASE_URL` – your public URL (e.g. from `ngrok http 5001`)
- `PAYPAL_WEBHOOK_ID` – create a webhook in the developer dashboard pointing to `<BASE_URL>/webhook`
- `PLAN_ID_BASIC` / `PLAN_ID_PRO` – run `uv run setup_plans.py` once and paste the printed IDs

Run it:

```bash
uv run app.py
```

Open http://localhost:5001.

> **Warning:** The apps run with `debug=True`. Exposing them through ngrok in debug mode is a security risk (the Werkzeug debugger allows remote code execution). Set `debug=False` before tunneling.

## Docs

- [PayPal JavaScript SDK v6](https://docs.paypal.ai/reference/sdk/js/v6/reference)
- [PayPal Python Server SDK](https://github.com/paypal/PayPal-Python-Server-SDK)
