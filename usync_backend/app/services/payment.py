from stripe import StripeClient
from services import STRIPE_CURRENCY, STRIPE_PAYMENT_METHOD_TYPES

async def makeFormPayment(stripeClient: StripeClient, payload: dict[str, str | int]):
    """
    Asynchronous service to make a form payment. This will be executed via the /payment/form endpoint.

    ::param id the string statement descriptor id
    ::param amount the integer representation of the price to pay
    ::param team_name the string representing the team that is signing up with this payment
    ::param event_name the string representing the event the team is signing up for
    ::param user_id the uuid for the user who is paying to ensure we know who paid
    """

    paymentIntent = {
        "amount": payload["amount"],
        "currency": STRIPE_CURRENCY,
        "statement_descriptor": payload["id"],
        "payment_method_types": STRIPE_PAYMENT_METHOD_TYPES,
        "metadata": {
            "team_name": payload["team_name"],
            "event_name": payload["event_name"],
            "user_id": payload["user_id"]
        }
    }

    payment = await stripeClient.v1.payment_intents.create_async(paymentIntent)
    # Do post payment processing stuff?? if we don't need then delete payments and leave the intents.
    