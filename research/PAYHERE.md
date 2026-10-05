# PayHere notification research

Everything payhere-sim imitates, and where it comes from. Checked 2026-10-05.

## Sources

- Checkout API: https://support.payhere.lk/api-&-mobile-sdk/checkout-api
- Recurring API: https://support.payhere.lk/api-&-mobile-sdk/recurring-api
- Preapproval API: https://support.payhere.lk/api-&-mobile-sdk/preapproval-api

## Checkout form (what your site posts to PayHere)

Posted to `https://sandbox.payhere.lk/pay/checkout` (sandbox) or `https://www.payhere.lk/pay/checkout` (live).

Required: `merchant_id`, `return_url`, `cancel_url`, `notify_url`, `first_name`, `last_name`, `email`,
`phone`, `address`, `city`, `country`, `order_id`, `items`, `currency`, `amount`, `hash`
(`hash` required since 2023-01-16).

Optional: `delivery_address`, `delivery_city`, `delivery_country`, `item_name_N`, `item_number_N`,
`amount_N`, `quantity_N`, `platform`, `custom_1`, `custom_2`, `payment_method`.

Currencies: LKR, USD, GBP, EUR, AUD.

Checkout hash:

```
hash = upper(md5(merchant_id + order_id + amount + currency + upper(md5(merchant_secret))))
```

`amount` is formatted with two decimals and no thousands separator (`number_format($amount, 2, '.', '')`).
A wrong hash gives "Unauthorized payment request". The docs list causes: value mismatch, unregistered domain,
wrong merchant secret, missing Referer header.

No payment status is passed to `return_url`; the site must read its own database.

## Payment notification (what PayHere posts to `notify_url`)

`POST`, `application/x-www-form-urlencoded` (not JSON). Fields:

| Field | Meaning |
|---|---|
| `merchant_id` | Merchant ID |
| `order_id` | The order ID the merchant sent |
| `payment_id` | Unique ID PayHere gives this payment |
| `payhere_amount` | Amount paid |
| `payhere_currency` | LKR, USD, GBP, EUR, AUD |
| `status_code` | 2 success, 0 pending, -1 cancelled, -2 failed, -3 charged back |
| `md5sig` | Signature (below) |
| `custom_1`, `custom_2` | Echoed from checkout |
| `method` | VISA, MASTER, AMEX, EZCASH, MCASH, GENIE, VISHWA, PAYAPP, HNB, FRIMI |
| `status_message` | Message from the gateway |
| `card_holder_name`, `card_no` (masked, `************4564`), `card_expiry` (MMYY) | Card payments only |

Signature:

```
md5sig = upper(md5(merchant_id + order_id + payhere_amount + payhere_currency + status_code + upper(md5(merchant_secret))))
```

Preapproval notifications add `customer_token`. Recurring notifications add `subscription_id`, `recurring`,
`item_recurrence`, `item_duration`, `item_rec_status`, `item_rec_date_next`, `item_rec_install_paid`
and `message_type` (`AUTHORIZATION_SUCCESS`, `RECURRING_INSTALLMENT_SUCCESS`, ...). Same signature.

## What the docs do not say

- **Retries and duplicates.** Not documented. Developer reports say PayHere retries when the endpoint
  doesn't return HTTP 200, so the same notification can arrive more than once. payhere-sim treats
  duplicates as something every handler must survive, which is true for any webhook regardless.
- **Ordering.** Not documented. Each payment attempt gets its own `payment_id`, so a failed attempt's
  notification can arrive after a later successful one.
- **Localhost.** "You cannot test the payment notification on localhost." This is the gap payhere-sim fills.

## Prior art (checked PyPI, npm, GitHub)

- SDKs and UI wrappers (`payhere-node`, `react-payhere`, mobile SDK plugins), and docs MCP servers.
- One demo e-commerce app has a built-in "webhook testbed" page for its own handler.
- No standalone tool that signs notifications, checks handler safety, or runs a local checkout.
  The names `payhere-sim` on PyPI and npm were free.
