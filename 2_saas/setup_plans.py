import os

import requests
from dotenv import load_dotenv

from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient
from paypalserversdk.configuration import Environment
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.models.plan_request import PlanRequest
from paypalserversdk.models.subscription_billing_cycle import SubscriptionBillingCycle
from paypalserversdk.models.frequency import Frequency
from paypalserversdk.models.subscription_pricing_scheme import SubscriptionPricingScheme
from paypalserversdk.models.money import Money
from paypalserversdk.models.payment_preferences import PaymentPreferences

load_dotenv()

CLIENT_ID = os.environ["PAYPAL_CLIENT_ID"]
CLIENT_SECRET = os.environ["PAYPAL_CLIENT_SECRET"]
PAYPAL_API = "https://api-m.sandbox.paypal.com"

paypal = PaypalServersdkClient(
    client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
        o_auth_client_id=CLIENT_ID,
        o_auth_client_secret=CLIENT_SECRET,
    ),
    environment=Environment.SANDBOX,
)


def get_access_token():
    res = requests.post(
        f"{PAYPAL_API}/v1/oauth2/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    res.raise_for_status()
    return res.json()["access_token"]


def create_product():
    res = requests.post(
        f"{PAYPAL_API}/v1/catalogs/products",
        headers={
            "Authorization": f"Bearer {get_access_token()}",
            "Content-Type": "application/json",
        },
        json={
            "name": "Demo SaaS",
            "description": "Demo SaaS subscription product",
            "type": "SERVICE",
            "category": "SOFTWARE",
        },
        timeout=10,
    )
    res.raise_for_status()
    return res.json()["id"]


def create_plan(product_id, name, price):
    plan = paypal.subscriptions.create_billing_plan({
        "body": PlanRequest(
            product_id=product_id,
            name=name,
            billing_cycles=[
                SubscriptionBillingCycle(
                    tenure_type="REGULAR",
                    sequence=1,
                    total_cycles=0,
                    frequency=Frequency(interval_unit="MONTH", interval_count=1),
                    pricing_scheme=SubscriptionPricingScheme(
                        fixed_price=Money(currency_code="USD", value=price)
                    ),
                )
            ],
            payment_preferences=PaymentPreferences(
                auto_bill_outstanding=True,
                payment_failure_threshold=3,
            ),
        ),
        "prefer": "return=representation",
    })
    return plan.body.id


if __name__ == "__main__":
    product_id = create_product()
    print(f"Product: {product_id}\n")
    print(f"PLAN_ID_BASIC={create_plan(product_id, 'Basic Plan', '9.99')}")
    print(f"PLAN_ID_PRO={create_plan(product_id, 'Pro Plan', '29.99')}")
    print("\nPaste these into your .env file.")
