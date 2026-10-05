# payhere-sim

**Test your PayHere integration on your own machine.** Send correctly signed payment notifications to
`localhost`, check that your `notify_url` handler survives duplicates, late and forged notifications, and
click through a local checkout page that explains a wrong hash instead of saying "Unauthorized payment request".

![payhere-sim check finding five bugs in a PayHere handler](https://raw.githubusercontent.com/ShalomHunukumbura/payhere-sim/main/docs/demo.gif)

The handler in that demo checks the signature and even skips orders that are already paid. It still gives a
customer **500 credits for one Rs. 1,000 payment** when PayHere's retries arrive together.

## Why

[PayHere](https://www.payhere.lk) tells your server about a payment by POSTing to your `notify_url`, but
"you cannot test the payment notification on localhost". So most integrations are tested by clicking through
the sandbox once, behind a tunnel, with the happy path only. The bugs that cost money are on the other paths:

- **Duplicates.** A notification can arrive more than once, sometimes at the same moment.
- **Order.** A failed attempt's notification can arrive after the successful one.
- **Forgeries.** Anyone can POST to `notify_url`.
- **Wrong amounts.** If the checkout hash is made in the browser, a customer can change the price.

payhere-sim sends all of these, from your machine, signed exactly as PayHere signs them.

## Install

```bash
pipx install payhere-sim     # or: uv tool install payhere-sim, or pip install payhere-sim
```

No dependencies beyond Python 3.10+. Give it the merchant ID and secret your app uses:

```bash
export PAYHERE_MERCHANT_ID=1234567
export PAYHERE_MERCHANT_SECRET=your-sandbox-secret
```

## 1. Check your handler: `payhere-sim check`

```bash
payhere-sim check http://localhost:8000/payhere/notify \
    --new-order http://localhost:8000/orders \
    --probe 'http://localhost:8000/orders/{order_id}'
```

payhere-sim can't see your database, so it needs two hooks into your app:

- `--new-order`: POST here to create a fresh unpaid order. Return JSON with `order_id` (and `amount`,
  `currency`, or pass `--amount`). Each scenario uses its own order, so one bug doesn't hide another.
- `--probe`: GET here to read an order's state as JSON. Return whatever matters: status, credits granted,
  emails sent, stock reserved. Anything that would be wrong if a payment were processed twice.

Both have `--new-order-cmd` / `--probe-cmd` versions that run a shell command instead (a `psql` query, a
management command), so you don't need to add test endpoints. Use `--ignore updated_at` to leave fields
like timestamps out of the comparisons.

| Check | Sends | Passes if the state... |
|---|---|---|
| Forged signature | success, signed with the wrong secret | doesn't change |
| Underpaid | correctly signed success for 1% of the total | doesn't change |
| Wrong currency | correctly signed success in another currency | doesn't change |
| Success | pending, then success | changes |
| Duplicate | the same success again | stays as after the first |
| Late pending | the earlier pending again | stays paid |
| Late failure | a failed attempt with an older payment ID | stays paid |
| Simultaneous duplicates | 5 identical successes at once, on a new order | matches one success exactly |
| HTTP 2xx | (every valid notification above) | each got a 2xx |
| Chargeback | status -3 | changes (warning only) |

Each failure says what changed and how to fix it. The exit code is 1 if anything failed, so it runs in CI.

## 2. Click through a checkout locally: `payhere-sim serve`

```bash
payhere-sim serve        # http://localhost:9090
```

Point your checkout form at `http://localhost:9090/pay/checkout` instead of
`https://sandbox.payhere.lk/pay/checkout`. payhere-sim:

- checks every required field, the merchant ID, currency and amount;
- verifies the `hash`, and if it's wrong, works out the usual mistake (amount not formatted as `1000.00`,
  secret not hashed or not uppercased, lowercase hash, fields in the wrong order);
- shows a payment page where you choose success, failure, pending or cancel;
- sends the signed notification to your `notify_url` (localhost is fine) and shows your app's response;
- lets you resend it, send a chargeback, a late failure or a forgery from the dashboard.

<img src="https://raw.githubusercontent.com/ShalomHunukumbura/payhere-sim/main/docs/checkout.png" alt="The local checkout page" width="560">

## 3. Send one notification: `payhere-sim send`

```bash
payhere-sim send http://localhost:8000/payhere/notify --order-id 42 --amount 1000          # success
payhere-sim send ... --status failed            # pending | cancelled | failed | chargedback
payhere-sim send ... --payment-id 320012345678 --repeat 3    # the same notification three times
payhere-sim send ... --bad-signature            # signed with the wrong secret
payhere-sim send ... --field customer_token=abc # extra fields (preapproval, recurring)
payhere-sim send ... --curl                     # print it as a curl command instead
```

And `payhere-sim hash --order-id 42 --amount 1000` prints the checkout hash your form should send.

## In your tests

```python
from payhere_sim import build, verify_notification

fields = build(merchant_id="1234567", merchant_secret="secret", order_id="42", amount="1000.00")
response = client.post("/payhere/notify", data=fields)       # e.g. a FastAPI / Django test client
```

## Example

[`examples/credits-shop`](https://github.com/ShalomHunukumbura/payhere-sim/tree/main/examples/credits-shop) is a small FastAPI shop with a buggy and a safe handler.
The safe one passes every check; the difference is a few lines:

```python
# buggy: two simultaneous requests both see "unpaid" and both add credits
if order["status"] == "paid":
    return
send_receipt_email(order["id"])
conn.execute("update orders set status = 'paid' ...")
conn.execute("update users set credits = credits + 100 ...")

# safe: one atomic update decides who fulfils the order
paid = conn.execute("update orders set status = 'paid' ... where id = ? and status != 'paid'", ...).rowcount
if paid:
    conn.execute("update users set credits = credits + 100 ...")
```

```bash
cd examples/credits-shop
SHOP_BUGGY=1 uv run --with fastapi --with uvicorn --with python-multipart uvicorn app:app --port 8000
```

## What it's based on

The fields, status codes and both MD5 signatures follow PayHere's
[Checkout API](https://support.payhere.lk/api-&-mobile-sdk/checkout-api) documentation; the signatures are
also tested against an independent implementation. The docs don't describe retries or ordering, so the
duplicate and ordering checks are what any webhook handler should survive rather than a model of
PayHere's exact retry schedule. Notes and sources: [`research/PAYHERE.md`](https://github.com/ShalomHunukumbura/payhere-sim/blob/main/research/PAYHERE.md).

Not covered yet: the JavaScript SDK's popup (`payhere.startPayment`), and recurring/preapproval checkouts
in `serve` (their notifications can be sent with `--field`).

Not affiliated with PayHere.

## License

MIT
