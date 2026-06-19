#!/usr/bin/env python3
"""Attach online invoice URL to credentials/xero_layout_verification.json."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations import xero

PATH = ROOT / "credentials" / "xero_layout_verification.json"


def main() -> int:
    data = json.loads(PATH.read_text())
    invoice_id = data.get("invoice_id", "")
    online = xero._api_request("GET", "Invoices/{0}/OnlineInvoice".format(invoice_id))
    data["online_invoice_url"] = (
        (online.get("OnlineInvoices") or [{}])[0].get("OnlineInvoiceUrl") or ""
    )
    PATH.write_text(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
