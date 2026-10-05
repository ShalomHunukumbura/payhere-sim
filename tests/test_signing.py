import hashlib
import shutil
import subprocess

import pytest

from payhere_sim.signing import (checkout_hash, diagnose_checkout_hash, format_amount, notification_sig,
                                 verify_notification)

MID, SECRET = "1234567", "demo-secret"

# Pinned values, cross-checked against Node's crypto (see test_matches_an_independent_implementation).
CHECKOUT = "028E0CC8A0A0657359EF8D7086C4D468"  # 1234567 / ORD-1 / 1000.00 / LKR
NOTIFY = "EE2B0589E45DF098B14877F998DBA283"    # ... / status 2


def test_pinned_values():
    assert checkout_hash(MID, "ORD-1", "1000", "LKR", SECRET) == CHECKOUT
    assert notification_sig(MID, "ORD-1", "1000.00", "LKR", 2, SECRET) == NOTIFY


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_matches_an_independent_implementation():
    js = ('const c=require("crypto");const m=s=>c.createHash("md5").update(s).digest("hex").toUpperCase();'
          'const s=m("demo-secret");console.log(m("1234567ORD-11000.00LKR"+s)+" "+m("1234567ORD-11000.00LKR2"+s))')
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.split()
    assert out == [CHECKOUT, NOTIFY]


@pytest.mark.parametrize("raw,expected", [
    ("1000", "1000.00"), (1000, "1000.00"), ("1000.5", "1000.50"), ("0.005", "0.01"),
    ("1234.565", "1234.57"), (" 99.9 ", "99.90"), ("1E+3", "1000.00"),
])
def test_format_amount(raw, expected):
    assert format_amount(raw) == expected


@pytest.mark.parametrize("bad", ["1,000.00", "abc", "", "NaN", "Infinity"])
def test_format_amount_rejects(bad):
    with pytest.raises(ValueError):
        format_amount(bad)


def test_verify_notification():
    fields = dict(merchant_id=MID, order_id="ORD-1", payhere_amount="1000.00", payhere_currency="LKR",
                  status_code="2", md5sig=NOTIFY)
    assert verify_notification(fields, SECRET)
    assert not verify_notification({**fields, "status_code": "-2"}, SECRET)
    assert not verify_notification(fields, "wrong")
    assert not verify_notification({}, SECRET)


def md5u(s):
    return hashlib.md5(s.encode()).hexdigest().upper()


SECRET_MD5 = md5u(SECRET)
FORM = dict(merchant_id=MID, order_id="ORD-1", amount="1000", currency="LKR")


@pytest.mark.parametrize("wrong_hash,hint", [
    (CHECKOUT.lower(), "lowercase"),
    (md5u(f"{MID}ORD-11000LKR{SECRET_MD5}"), "two decimals"),
    (md5u(f"{MID}ORD-11000.00LKR{SECRET}"), "Hash it first"),
    (md5u(f"{MID}ORD-11000.00LKR{SECRET_MD5.lower()}"), "must be uppercased"),
    (md5u(f"{MID}ORD-1LKR1000.00{SECRET_MD5}"), "swapped"),
    (md5u(f"{MID}ORD-11000.00LKR"), "secret hash is missing"),
])
def test_diagnose_common_mistakes(wrong_hash, hint):
    hints = diagnose_checkout_hash({**FORM, "hash": wrong_hash}, SECRET)
    assert any(hint in h for h in hints), hints


def test_diagnose_spaces():
    form = {**FORM, "order_id": "ORD-1 ", "amount": "1000.00", "hash": CHECKOUT}
    assert any("extra spaces" in h for h in diagnose_checkout_hash(form, SECRET))


def test_diagnose_unknown_mistake():
    assert diagnose_checkout_hash({**FORM, "hash": "0" * 32}, SECRET) == []
