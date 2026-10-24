"""
Full pre-print verification pipeline.

Stage A: generate 50 TEST tickets, insert into a separate "test_tickets"
         collection (never touched by the real app), and exercise the exact
         same atomic MongoDB query logic the live /api/scan/checkin endpoint
         uses — first scan admits, duplicate scan rejected, cancelled
         rejected, unknown code rejected — against the REAL Atlas cluster.
         Saved to its own PDF, completely separate from production.

Stage B: only if every check in Stage A passes, wipe the real "tickets"
         collection and generate the real 400, with the same uniqueness +
         QR round-trip verification used before, then insert + verify.

Nothing in Stage B runs unless Stage A passes completely.
"""

import csv
import os
import re
import sys
from datetime import datetime, timezone

UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import fitz
from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode
from pymongo import MongoClient

from app.pdf_gen import build_tickets
from app.config import settings

PROD_COUNT = 400
TEST_COUNT = 50
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "production_output")


def verify_uniqueness_and_qr(rows, pdf_bytes, expected_count, label):
    assert len(rows) == expected_count, f"[{label}] FATAL: expected {expected_count} rows, got {len(rows)}"

    ids = [r["ticket_id"] for r in rows]
    assert len(set(ids)) == expected_count, f"[{label}] FATAL: duplicate UUIDs found"
    codes = [r["short_code"] for r in rows]
    assert len(set(codes)) == expected_count, f"[{label}] FATAL: duplicate short codes found"
    print(f"[{label}] [OK] {expected_count} unique UUIDs and short codes")

    # --- Every ID must actually be a random UUIDv4, not a sequential/predictable value ---
    bad_format = [i for i in ids if not UUID_V4_RE.match(i)]
    assert not bad_format, f"[{label}] FATAL: {len(bad_format)} IDs are not valid random UUIDv4s: {bad_format[:5]}"
    print(f"[{label}] [OK] All {expected_count} IDs are valid random UUIDv4 (version/variant bits correct)")

    # A sequential/incrementing generator would produce IDs whose sort order
    # matches generation order. Random UUIDs essentially never do.
    assert ids != sorted(ids), f"[{label}] FATAL: IDs are in sorted order — looks sequential, not random"
    # Same check on the short codes (derived from the UUIDs) for good measure.
    assert codes != sorted(codes), f"[{label}] FATAL: short codes are in sorted order — looks sequential"
    print(f"[{label}] [OK] Confirmed not sequential (generation order != sorted order)")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) == expected_count, f"[{label}] FATAL: PDF has {len(doc)} pages, expected {expected_count}"

    mismatches = []
    for i, row in enumerate(rows):
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(3, 3))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        results = zbar_decode(img)
        if not results:
            mismatches.append((i, row["ticket_id"], "NO QR DETECTED"))
            continue
        decoded = results[0].data.decode("utf-8")
        if decoded != row["ticket_id"]:
            mismatches.append((i, row["ticket_id"], decoded))

    if mismatches:
        print(f"[{label}] FATAL: {len(mismatches)} QR mismatches:")
        for m in mismatches:
            print(" ", m)
        sys.exit(1)
    print(f"[{label}] [OK] All {expected_count} QR codes decode to their exact intended UUID")

    assert all(r["status"] == "valid" for r in rows), f"[{label}] FATAL: not all rows are status='valid'"
    print(f"[{label}] [OK] All {expected_count} rows are status='valid'")


def simulate_checkin(coll, code, scanned_by="test-harness"):
    """Exact same query shape as app/routes/scan.py:checkin()."""
    result = coll.find_one_and_update(
        {"_id": code, "status": "valid"},
        {"$set": {"status": "used", "used_at": datetime.now(timezone.utc).isoformat(), "scanned_by": scanned_by}},
        return_document=True,
    )
    if result:
        return {"ok": True, "reason": "admitted"}
    existing = coll.find_one({"_id": code})
    if not existing:
        return {"ok": False, "reason": "not_found"}
    if existing["status"] == "cancelled":
        return {"ok": False, "reason": "cancelled"}
    return {"ok": False, "reason": "already_used"}


def stage_a_test_batch(client):
    print(f"\n=== STAGE A: {TEST_COUNT} TEST tickets (separate collection + PDF) ===")
    rows, pdf_bytes = build_tickets(TEST_COUNT)
    verify_uniqueness_and_qr(rows, pdf_bytes, TEST_COUNT, "TEST")

    db = client[settings.mongodb_db]
    test_coll = db["test_tickets"]

    existing = test_coll.count_documents({})
    if existing:
        print(f"[TEST] Wiping {existing} pre-existing doc(s) from test_tickets...")
        test_coll.delete_many({})

    test_coll.insert_many(rows, ordered=True)
    db_count = test_coll.count_documents({})
    assert db_count == TEST_COUNT, f"[TEST] FATAL: test_tickets has {db_count} docs, expected {TEST_COUNT}"
    print(f"[TEST] [OK] Inserted and verified {TEST_COUNT} docs in 'test_tickets' collection (separate from 'tickets')")

    # --- Live behavior tests against the REAL Atlas cluster, same query shapes as production code ---
    sample = rows[0]["ticket_id"]
    cancelled_sample = rows[1]["ticket_id"]
    unknown_code = "00000000-0000-0000-0000-000000000000"

    test_coll.update_one({"_id": cancelled_sample}, {"$set": {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()}})

    r1 = simulate_checkin(test_coll, sample)
    assert r1["ok"] and r1["reason"] == "admitted", f"[TEST] FATAL: first scan of a valid ticket should admit, got {r1}"
    print("[TEST] [OK] First scan of a valid ticket -> admitted")

    r2 = simulate_checkin(test_coll, sample)
    assert not r2["ok"] and r2["reason"] == "already_used", f"[TEST] FATAL: duplicate scan should be rejected, got {r2}"
    print("[TEST] [OK] Duplicate scan of the same ticket -> rejected (already_used)")

    r3 = simulate_checkin(test_coll, cancelled_sample)
    assert not r3["ok"] and r3["reason"] == "cancelled", f"[TEST] FATAL: cancelled ticket should be rejected, got {r3}"
    print("[TEST] [OK] Scan of a cancelled ticket -> rejected (cancelled)")

    r4 = simulate_checkin(test_coll, unknown_code)
    assert not r4["ok"] and r4["reason"] == "not_found", f"[TEST] FATAL: unknown code should 404, got {r4}"
    print("[TEST] [OK] Scan of an unknown/fabricated code -> rejected (not_found)")

    # cleanup the mutations so test_tickets is left in a clean "all valid" state for manual testing
    test_coll.update_many({}, {"$set": {"status": "valid", "used_at": None, "scanned_by": None, "cancelled_at": None}})
    print("[TEST] Reset test_tickets back to all-valid for manual scanning tests, if you want to try the real app.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUTPUT_DIR, f"TEST_tickets_{TEST_COUNT}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"[TEST] Saved separate test PDF: {pdf_path}")

    print("\n=== STAGE A PASSED: all live database behaviors verified correct on real Atlas cluster ===")


def stage_b_production_batch(client):
    print(f"\n=== STAGE B: {PROD_COUNT} PRODUCTION tickets ===")
    rows, pdf_bytes = build_tickets(PROD_COUNT)
    verify_uniqueness_and_qr(rows, pdf_bytes, PROD_COUNT, "PROD")

    db = client[settings.mongodb_db]
    coll = db["tickets"]

    existing = coll.count_documents({})
    if existing:
        print(f"[PROD] Wiping {existing} existing doc(s) from tickets collection (regenerating design)...")
        deleted = coll.delete_many({}).deleted_count
        print(f"[PROD] Deleted {deleted} doc(s).")

    coll.insert_many(rows, ordered=True)
    print(f"[PROD] Inserted {PROD_COUNT} documents.")

    db_count = coll.count_documents({})
    assert db_count == PROD_COUNT, f"[PROD] FATAL: DB has {db_count} docs, expected {PROD_COUNT}"
    valid_count = coll.count_documents({"status": "valid"})
    assert valid_count == PROD_COUNT, f"[PROD] FATAL: DB has {valid_count} valid docs, expected {PROD_COUNT}"
    db_ids = set(doc["_id"] for doc in coll.find({}, {"_id": 1}))
    assert db_ids == set(r["ticket_id"] for r in rows), "[PROD] FATAL: DB ID set does not match generated set"
    print(f"[PROD] [OK] Verified: DB has exactly {PROD_COUNT} tickets, all valid, IDs match generated set exactly")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUTPUT_DIR, f"gayatri_maharas_tickets_{PROD_COUNT}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"[PROD] Saved printable PDF: {pdf_path}")

    csv_path = os.path.join(OUTPUT_DIR, f"tickets_{PROD_COUNT}_backup.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["seq", "short_code", "ticket_id", "status"])
        writer.writeheader()
        for r in sorted(rows, key=lambda r: r["seq"]):
            writer.writerow({"seq": r["seq"], "short_code": r["short_code"], "ticket_id": r["ticket_id"], "status": r["status"]})
    print(f"[PROD] Saved backup CSV: {csv_path}")

    print(f"\n=== STAGE B PASSED. {PROD_COUNT} production tickets are live in the database. ===")
    print("Nothing has been printed. Review the PDF before printing.")


def main():
    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15000)
    client.admin.command("ping")

    stage_a_test_batch(client)
    stage_b_production_batch(client)

    client.close()


if __name__ == "__main__":
    main()
