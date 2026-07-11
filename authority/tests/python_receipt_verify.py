from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from superhuman_mail import approval

payload = json.load(sys.stdin)
roots = {
    payload["issuer"]: {
        "key_id": payload["key_id"],
        "public_key": payload["public_key"],
        "allowed_approvers": [payload["approver"]],
    }
}
verified = approval.verify(
    payload["receipt"],
    attestation=payload["attestation"],
    roots=roots,
    now=datetime.fromtimestamp(payload["now_ms"] / 1000, timezone.utc),
)
json.dump({"receipt_id": verified["receipt_id"], "binding": verified["binding"]}, sys.stdout)
sys.stdout.write("\n")
