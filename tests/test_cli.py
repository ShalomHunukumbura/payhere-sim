import subprocess
import sys

from payhere_sim.cli import main

from .conftest import MERCHANT_ID, SECRET

CREDS = ["--merchant-id", MERCHANT_ID, "--secret", SECRET]


def test_hash(capsys):
    assert main(["hash", *CREDS, "--order-id", "ORD-1", "--amount", "1000"]) == 0
    assert capsys.readouterr().out.strip() == "028E0CC8A0A0657359EF8D7086C4D468"


def test_credentials_from_env(monkeypatch, capsys):
    monkeypatch.setenv("PAYHERE_MERCHANT_ID", MERCHANT_ID)
    monkeypatch.setenv("PAYHERE_MERCHANT_SECRET", SECRET)
    assert main(["hash", "--order-id", "ORD-1", "--amount", "1000.00"]) == 0
    assert capsys.readouterr().out.strip() == "028E0CC8A0A0657359EF8D7086C4D468"


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("PAYHERE_MERCHANT_SECRET", raising=False)
    try:
        main(["hash", "--merchant-id", "1", "--order-id", "1", "--amount", "1"])
    except SystemExit as e:
        assert "PAYHERE_MERCHANT_SECRET" in str(e.code)
    else:
        raise AssertionError("expected an error")


def test_send_repeat_and_exit_code(recorder, capsys):
    args = ["send", recorder.url, *CREDS, "--order-id", "7", "--amount", "50", "--repeat", "2", "--field", "x=1"]
    assert main(args) == 0
    assert len(recorder.requests) == 2 and recorder.requests[0][1] == recorder.requests[1][1]
    assert recorder.requests[0][1]["x"] == "1" and "[2/2]" in capsys.readouterr().out
    recorder.status = 400
    assert main(args[:-4]) == 1


def test_send_curl(capsys):
    assert main(["send", "http://x/n", *CREDS, "--order-id", "7", "--amount", "50", "--curl"]) == 0
    assert "--data-urlencode 'payhere_amount=50.00'" in capsys.readouterr().out


def test_check_end_to_end(shops):
    base = shops["buggy"]
    done = subprocess.run(
        [sys.executable, "-m", "payhere_sim", "check", f"{base}/payhere/notify", *CREDS,
         "--new-order", f"{base}/orders", "--probe", base + "/orders/{order_id}"],
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "✗ processes simultaneous duplicates only once" in done.stdout
    assert "5 passed, 5 failed" in done.stdout


def test_check_reports_probe_failure(shops):
    base = shops["safe"]
    done = subprocess.run(
        [sys.executable, "-m", "payhere_sim", "check", f"{base}/payhere/notify", *CREDS,
         "--new-order", f"{base}/orders", "--probe", base + "/nope/{order_id}"],
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 2 and "returned HTTP 404" in done.stdout
