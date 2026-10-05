"""PayHere's two MD5 signatures: the checkout hash and the notification md5sig.

Formulas from https://support.payhere.lk/api-&-mobile-sdk/checkout-api
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CURRENCIES = ("LKR", "USD", "GBP", "EUR", "AUD")


def _md5_upper(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest().upper()


def format_amount(amount) -> str:
    """Two decimals, no thousands separator: what PayHere signs (PHP number_format($a, 2, '.', ''))."""
    try:
        value = Decimal(str(amount).strip())
    except InvalidOperation:
        raise ValueError(f"amount must be a number, got {amount!r}") from None
    if not value.is_finite():
        raise ValueError(f"amount must be a number, got {amount!r}")
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def secret_hash(merchant_secret: str) -> str:
    return _md5_upper(merchant_secret)


def checkout_hash(merchant_id, order_id, amount, currency, merchant_secret) -> str:
    """The `hash` field your site sends with the checkout form."""
    return _md5_upper(f"{merchant_id}{order_id}{format_amount(amount)}{currency}{secret_hash(merchant_secret)}")


def notification_sig(merchant_id, order_id, payhere_amount, payhere_currency, status_code, merchant_secret) -> str:
    """The `md5sig` PayHere sends with every notification. `payhere_amount` is signed exactly as sent."""
    return _md5_upper(
        f"{merchant_id}{order_id}{payhere_amount}{payhere_currency}{status_code}{secret_hash(merchant_secret)}"
    )


def verify_notification(fields: dict, merchant_secret: str) -> bool:
    """What your notify_url handler should do before trusting a notification."""
    expected = notification_sig(
        fields.get("merchant_id", ""), fields.get("order_id", ""), fields.get("payhere_amount", ""),
        fields.get("payhere_currency", ""), fields.get("status_code", ""), merchant_secret,
    )
    return fields.get("md5sig", "") == expected


def diagnose_checkout_hash(form: dict, merchant_secret: str) -> list[str]:
    """Explain a wrong checkout hash by recomputing it with the usual mistakes.

    Returns hints, most likely first. Empty if no known mistake matches.
    """
    mid, oid = form.get("merchant_id", ""), form.get("order_id", "")
    amount, currency = form.get("amount", ""), form.get("currency", "")
    sent = form.get("hash", "")
    secret_md5 = secret_hash(merchant_secret)
    try:
        formatted = format_amount(amount)
    except ValueError:
        formatted = amount

    def h(*parts):
        return _md5_upper("".join(str(p) for p in parts))

    candidates = [
        (h(mid, oid, formatted, currency, secret_md5).lower(),
         "The hash is right but in lowercase. PayHere's formula uppercases it: use .upper() / strtoupper()."),
        (h(mid, oid, amount, currency, secret_md5),
         f"You hashed the amount as {amount!r}. Hash it formatted with two decimals and no commas: {formatted!r}."),
        (h(mid, oid, formatted, currency, merchant_secret),
         "You used the merchant secret directly. Hash it first: upper(md5(merchant_secret))."),
        (h(mid, oid, formatted, currency, secret_md5.lower()),
         "The md5 of the merchant secret must be uppercased before it goes into the hash."),
        (h(mid, oid, currency, formatted, secret_md5),
         "Amount and currency are swapped. The order is merchant_id + order_id + amount + currency + secret hash."),
        (h(mid, oid, formatted, currency),
         "The secret hash is missing from the end: ... + currency + upper(md5(merchant_secret))."),
        (h(*(str(x).strip() for x in (mid, oid, formatted, currency)), secret_md5),
         "Some values have extra spaces. Hash exactly the values you post."),
    ]
    hints = [hint for value, hint in candidates if sent and sent == value]
    return list(dict.fromkeys(hints))
