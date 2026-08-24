"""Match imported bank transfers to invoices by Payment Reference / Invoice Number."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import database as db
import invoice
import invoice_numbering

# INV / Inv / inv, optional spaces or hyphen, then digits. Case-insensitive.
INV_TOKEN_RE = re.compile(r"INV[\s-]*(\d+)", re.I)
AMOUNT_RE = re.compile(r"-?\d+(?:[.,]\d{1,2})?")

STATUS_PAID = "paid"
STATUS_MISMATCH = "mismatch"
STATUS_UNMATCHED = "unmatched"
STATUS_SKIPPED = "skipped"

DATE_HEADERS = {
    "date",
    "transaction date",
    "txn date",
    "value date",
    "posted date",
    "trans date",
}
DESC_HEADERS = {
    "description",
    "particulars",
    "narrative",
    "details",
    "transaction description",
    "memo",
}
REF_HEADERS = {
    "reference",
    "ref",
    "bank reference",
    "payment reference",
    "tran particular",
    "customer reference",
}
CREDIT_HEADERS = {"credit", "credit amount", "credit amt"}
DEBIT_HEADERS = {"debit", "debit amount", "debit amt"}
AMOUNT_HEADERS = {"amount", "transaction amount", "value", "aud"} | CREDIT_HEADERS


def payment_reference_for_booking(booking: Dict[str, Any]) -> str:
    displayed = invoice_numbering.display_invoice_number(booking)
    if not displayed or displayed == "—":
        return ""
    return displayed


def extract_invoice_tokens(text: str) -> List[str]:
    """Unique INV{n} tokens from text (case-insensitive, optional space)."""
    found = []
    seen = set()
    for match in INV_TOKEN_RE.finditer(text or ""):
        formatted = "INV{0}".format(int(match.group(1)))
        if formatted not in seen:
            seen.add(formatted)
            found.append(formatted)
    return found


def invoice_search_text(parsed: Dict[str, Any]) -> str:
    """Combine Reference and Description so Westpac Narrative can be matched."""
    parts = [
        str(parsed.get("reference") or "").strip(),
        str(parsed.get("description") or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def parse_amount(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("$", "").replace("AUD", "").replace(",", "")
    text = text.replace("(", "").replace(")", "").strip()
    match = AMOUNT_RE.search(text)
    if not match:
        return None
    number = match.group(0).replace(",", "")
    try:
        amount = float(number)
    except ValueError:
        return None
    if negative:
        amount = -abs(amount)
    return round(amount, 2)


def parse_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%b-%Y",
        "%d %b %Y",
        "%d-%B-%Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(text[:32], fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("_", " ").strip().lower())


def _pick(row: Dict[str, str], names: Iterable[str]) -> str:
    for key, value in row.items():
        if _norm_header(key) in names:
            return str(value or "").strip()
    return ""


def fingerprint_for(
    transaction_date: str,
    description: str,
    reference: str,
    amount: float,
) -> str:
    payload = "|".join(
        [
            (transaction_date or "").strip(),
            re.sub(r"\s+", " ", (description or "").strip().lower()),
            re.sub(r"\s+", " ", (reference or "").strip().lower()),
            "{0:.2f}".format(float(amount)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_bank_csv_bytes(raw: bytes) -> List[Dict[str, Any]]:
    if not raw:
        return []
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return parse_bank_csv(text)


def parse_bank_csv(text: str) -> List[Dict[str, Any]]:
    raw = (text or "").lstrip("\ufeff")
    if not raw.strip():
        return []
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
    rows: List[Dict[str, Any]] = []
    for source in reader:
        mapped = {_norm_header(k): (v if v is not None else "") for k, v in source.items()}
        reference = _pick(mapped, REF_HEADERS)
        description = _pick(mapped, DESC_HEADERS)
        date_text = _pick(mapped, DATE_HEADERS)
        amount_text = _pick(mapped, AMOUNT_HEADERS)
        if not amount_text:
            credit = _pick(mapped, CREDIT_HEADERS)
            debit = _pick(mapped, DEBIT_HEADERS)
            if credit:
                amount_text = credit
            elif debit:
                parsed_debit = parse_amount(debit)
                amount_text = str(-abs(parsed_debit)) if parsed_debit is not None else ""
        amount = parse_amount(amount_text)
        if amount is None:
            continue
        if not any([date_text, description, reference]):
            continue
        rows.append(
            {
                "transaction_date": parse_date(date_text),
                "description": description,
                "reference": reference,
                "amount": amount,
            }
        )
    return rows


def _booking_invoice_total(booking: Dict[str, Any]) -> float:
    row = dict(booking)
    if "extra_charges" not in row and row.get("id"):
        row["extra_charges"] = db.list_extra_charges(int(row["id"]))
    elif "extra_charges" not in row:
        row["extra_charges"] = []
    return round(float(invoice.calculate_invoice_totals(row)["total"]), 2)


def _amounts_match(bank_amount: float, invoice_total: float) -> bool:
    return abs(round(float(bank_amount), 2) - round(float(invoice_total), 2)) < 0.005


def match_bank_transaction(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Return match fields for one imported bank row. Does not write bookings."""
    amount = round(float(parsed.get("amount") or 0), 2)
    tokens = extract_invoice_tokens(invoice_search_text(parsed))
    result = {
        "invoice_token": tokens[0] if tokens else "",
        "match_status": STATUS_UNMATCHED,
        "matched_booking_id": None,
        "invoice_total": None,
        "message": "",
    }
    if amount <= 0:
        result["message"] = "Ignored non-credit amount."
        result["match_status"] = STATUS_SKIPPED
        return result
    if not tokens:
        result["message"] = "No invoice number in Reference or Description."
        return result

    bookings: List[Dict[str, Any]] = []
    seen_ids = set()
    for token in tokens:
        for row in db.find_bookings_by_invoice_display(token):
            bid = int(row["id"])
            if bid not in seen_ids:
                seen_ids.add(bid)
                bookings.append(dict(row))
    if not bookings:
        result["message"] = "No invoice for {0}.".format(tokens[0])
        return result
    if len(bookings) > 1:
        result["match_status"] = STATUS_MISMATCH
        result["invoice_token"] = tokens[0]
        result["message"] = "Payment mismatch: multiple invoices for {0}.".format(
            tokens[0]
        )
        return result

    booking = bookings[0]
    total = _booking_invoice_total(booking)
    result["matched_booking_id"] = int(booking["id"])
    result["invoice_total"] = total
    result["invoice_token"] = payment_reference_for_booking(booking) or tokens[0]
    if not _amounts_match(amount, total):
        result["match_status"] = STATUS_MISMATCH
        result["message"] = (
            "Payment mismatch: {0} bank {1} vs invoice {2}.".format(
                result["invoice_token"],
                invoice.format_aud(amount),
                invoice.format_aud(total),
            )
        )
        return result

    current = invoice.normalize_payment_status(booking.get("payment_status"))
    result["match_status"] = STATUS_PAID
    if current == invoice.PAYMENT_STATUS_PAID:
        result["message"] = "{0} already Paid.".format(result["invoice_token"])
    else:
        result["message"] = "{0} matched {1}.".format(
            result["invoice_token"], invoice.format_aud(amount)
        )
    return result


def apply_paid_if_matched(match: Dict[str, Any]) -> bool:
    """Mark the linked booking Paid (and Completed) when this match is a new payment."""
    if match.get("match_status") != STATUS_PAID:
        return False
    booking_id = match.get("matched_booking_id")
    if not booking_id:
        return False
    row = db.get_booking(int(booking_id))
    if not row:
        return False
    current = invoice.normalize_payment_status(row["payment_status"])
    if current == invoice.PAYMENT_STATUS_PAID:
        return False
    invoice.apply_payment_status(int(booking_id), invoice.PAYMENT_STATUS_PAID)
    return True


def _match_fields(match: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "match_status": match.get("match_status") or STATUS_UNMATCHED,
        "matched_booking_id": match.get("matched_booking_id"),
        "invoice_total": match.get("invoice_total"),
        "invoice_token": match.get("invoice_token") or "",
        "message": match.get("message") or "",
    }


def _apply_and_store_existing(txn_id: int, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Re-match an existing unmatched row in place. Does not insert or delete."""
    match = match_bank_transaction(parsed)
    marked_paid = apply_paid_if_matched(match)
    db.update_bank_transaction(int(txn_id), _match_fields(match))
    return {**parsed, **match, "marked_paid": marked_paid, "id": int(txn_id)}


def _increment_status(paid: int, mismatches: int, unmatched: int, status: str):
    if status == STATUS_PAID:
        return paid + 1, mismatches, unmatched
    if status == STATUS_MISMATCH:
        return paid, mismatches + 1, unmatched
    if status == STATUS_UNMATCHED:
        return paid, mismatches, unmatched + 1
    return paid, mismatches, unmatched


def import_bank_transactions(parsed_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    imported = 0
    skipped = 0
    paid = 0
    mismatches = 0
    unmatched = 0
    rematched = 0
    results: List[Dict[str, Any]] = []
    for parsed in parsed_rows:
        fingerprint = fingerprint_for(
            parsed["transaction_date"],
            parsed["description"],
            parsed["reference"],
            parsed["amount"],
        )
        existing = db.get_bank_transaction_by_fingerprint(fingerprint)
        if existing:
            current_status = (existing.get("match_status") or "").strip()
            if current_status == STATUS_UNMATCHED:
                stored = _apply_and_store_existing(int(existing["id"]), parsed)
                rematched += 1
                paid, mismatches, unmatched = _increment_status(
                    paid, mismatches, unmatched, stored["match_status"]
                )
                results.append(stored)
            else:
                skipped += 1
                results.append(
                    {
                        **parsed,
                        "match_status": STATUS_SKIPPED,
                        "message": "Already imported.",
                    }
                )
            continue
        match = match_bank_transaction(parsed)
        marked_paid = apply_paid_if_matched(match)
        db.insert_bank_transaction(
            {
                "fingerprint": fingerprint,
                "transaction_date": parsed["transaction_date"],
                "description": parsed["description"],
                "reference": parsed["reference"],
                "amount": parsed["amount"],
                **_match_fields(match),
            }
        )
        imported += 1
        paid, mismatches, unmatched = _increment_status(
            paid, mismatches, unmatched, match["match_status"]
        )
        results.append({**parsed, **match, "marked_paid": marked_paid})
    return {
        "imported": imported,
        "skipped": skipped,
        "paid": paid,
        "mismatches": mismatches,
        "unmatched": unmatched,
        "rematched": rematched,
        "results": results,
    }


def rematch_unmatched_transactions() -> Dict[str, Any]:
    """Re-run matching on unmatched rows only. Never deletes bank_transactions."""
    rows = db.list_bank_transactions(match_status=STATUS_UNMATCHED, limit=2000)
    paid = 0
    mismatches = 0
    unmatched = 0
    skipped = 0
    rematched = 0
    results: List[Dict[str, Any]] = []
    for row in rows:
        parsed = {
            "transaction_date": row.get("transaction_date") or "",
            "description": row.get("description") or "",
            "reference": row.get("reference") or "",
            "amount": row.get("amount") or 0,
        }
        stored = _apply_and_store_existing(int(row["id"]), parsed)
        rematched += 1
        if stored["match_status"] == STATUS_PAID:
            paid += 1
        elif stored["match_status"] == STATUS_MISMATCH:
            mismatches += 1
        elif stored["match_status"] == STATUS_UNMATCHED:
            unmatched += 1
        elif stored["match_status"] == STATUS_SKIPPED:
            skipped += 1
        results.append(stored)
    return {
        "imported": 0,
        "skipped": skipped,
        "paid": paid,
        "mismatches": mismatches,
        "unmatched": unmatched,
        "rematched": rematched,
        "results": results,
    }


def list_payment_mismatches(limit: int = 20) -> List[Dict[str, Any]]:
    return db.list_bank_transactions(match_status=STATUS_MISMATCH, limit=limit)
