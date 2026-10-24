"""
ADDITIVE batch: generate 100 NEW tickets with a different sponsor set and
insert them ALONGSIDE the existing 400 (already printed and sold) — never
deleting, modifying, or re-touching anything in the 'tickets' collection
that already exists, and never writing to the three protected production
files from the first batch.

Safety model:
  1. Before touching anything: verify the DB currently has exactly the same
     400 tickets as the protected backup CSV. If this doesn't match exactly,
     abort immediately — something is already wrong and we should not add
     to it blind.
  2. Generate + fully verify the 100 new tickets completely offline (no DB
     writes) — uniqueness, UUIDv4 format, non-sequential, every QR decodes
     correctly, and none of the 100 new IDs collide with any existing ID.
  3. Only then insert_many() just the 100 new documents (never delete_many,
     never update existing docs).
  4. After insert: re-verify the original 400 are still byte-for-byte the
     same IDs as the backup CSV, AND the new 100 are present and valid, AND
     the total is exactly 500.
  5. Write outputs to NEW filenames only. Confirm the three protected files'
     mtimes are unchanged before vs after (proof nothing touched them).
"""

import csv
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import fitz
from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode
from pymongo import MongoClient

from app.pdf_gen import build_tickets
from app.ticket_design import ASSETS
from app.config import settings

UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

NEW_COUNT = 100
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "production_output")

PROTECTED_FILES = [
    os.path.join(OUTPUT_DIR, "gayatri_maharas_tickets_400.pdf"),
    os.path.join(OUTPUT_DIR, "TEST_tickets_50.pdf"),
    os.path.join(OUTPUT_DIR, "tickets_400_backup.csv"),
]

SPONSORS_BATCH2 = [
    (os.path.join(ASSETS, "Srishti Beauty Parlour .jpeg"), "Srishti Beauty Parlour"),
    (os.path.join(ASSETS, "Star Birds Pre-School.jpeg"), "Star Birds Preschool"),
    (os.path.join(ASSETS, "Raj Education Hub.jpeg"), "Raj Education Hub"),
    (os.path.join(ASSETS, "WhatsApp Image 2026-10-21 at 15.03.29.jpeg"), "Nritya Pathshala Studio"),
    (os.path.join(ASSETS, "WhatsApp Image 2026-10-21 at 15.04.26.jpeg"), "Swaraajbharti"),
]


def load_backup_ids(csv_path):
    ids = set()
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            ids.add(row["ticket_id"])
    return ids


def main():
    print("=== Pre-flight: confirming the 3 protected files exist and recording their mtimes ===")
    for p in PROTECTED_FILES:
        assert os.path.exists(p), f"FATAL: protected file missing: {p}"
    mtimes_before = {p: os.path.getmtime(p) for p in PROTECTED_FILES}
    for p in PROTECTED_FILES:
        print(f"  {os.path.basename(p)}")

    backup_csv = os.path.join(OUTPUT_DIR, "tickets_400_backup.csv")
    expected_old_ids = load_backup_ids(backup_csv)
    assert len(expected_old_ids) == 400, f"FATAL: backup CSV has {len(expected_old_ids)} IDs, expected 400"
    print(f"[OK] Loaded {len(expected_old_ids)} IDs from the protected backup CSV")

    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15000)
    client.admin.command("ping")
    db = client[settings.mongodb_db]
    coll = db["tickets"]

    print("\n=== Verifying the existing 400 in the database match the backup EXACTLY, before touching anything ===")
    current_count = coll.count_documents({})
    assert current_count == 400, f"FATAL: DB currently has {current_count} tickets, expected exactly 400 — aborting, will not add on top of an unexpected state"
    current_ids = set(doc["_id"] for doc in coll.find({}, {"_id": 1}))
    assert current_ids == expected_old_ids, "FATAL: DB ticket IDs do not exactly match the backup CSV — aborting"
    print("[OK] Confirmed: the 400 tickets in the database exactly match the sold/printed batch. Safe to proceed.")

    print(f"\n=== Generating {NEW_COUNT} NEW tickets (different sponsor set: removed Shikshit Bharat, added Nritya Pathshala Studio + Swaraajbharti) ===")
    rows, pdf_bytes = build_tickets(NEW_COUNT, sponsors=SPONSORS_BATCH2)
    assert len(rows) == NEW_COUNT, f"FATAL: expected {NEW_COUNT} rows, got {len(rows)}"

    ids = [r["ticket_id"] for r in rows]
    codes = [r["short_code"] for r in rows]
    assert len(set(ids)) == NEW_COUNT, "FATAL: duplicate UUIDs in new batch"
    assert len(set(codes)) == NEW_COUNT, "FATAL: duplicate short codes in new batch"
    print(f"[OK] {NEW_COUNT} unique UUIDs and short codes")

    bad_format = [i for i in ids if not UUID_V4_RE.match(i)]
    assert not bad_format, f"FATAL: {len(bad_format)} IDs are not valid UUIDv4"
    print("[OK] All IDs are valid random UUIDv4")

    assert ids != sorted(ids), "FATAL: IDs are in sorted order — looks sequential"
    assert codes != sorted(codes), "FATAL: short codes are in sorted order — looks sequential"
    print("[OK] Confirmed not sequential")

    # Cross-check against the OLD 400 — must be fully disjoint.
    collision = set(ids) & expected_old_ids
    assert not collision, f"FATAL: {len(collision)} new IDs collide with existing tickets: {collision}"
    print("[OK] Confirmed zero overlap with the existing 400 — fully disjoint ID sets")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) == NEW_COUNT, f"FATAL: PDF has {len(doc)} pages, expected {NEW_COUNT}"
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
        print(f"FATAL: {len(mismatches)} QR mismatches, aborting without touching the database:")
        for m in mismatches:
            print(" ", m)
        sys.exit(1)
    print(f"[OK] All {NEW_COUNT} QR codes decode to their exact intended UUID")

    assert all(r["status"] == "valid" for r in rows), "FATAL: not all new rows are status='valid'"
    print(f"[OK] All {NEW_COUNT} rows are status='valid'")

    print(f"\n=== All checks passed. Inserting {NEW_COUNT} NEW documents only (no delete, no update) ===")
    coll.insert_many(rows, ordered=True)
    print(f"Inserted {NEW_COUNT} new documents.")

    print("\n=== Post-insert verification ===")
    final_count = coll.count_documents({})
    assert final_count == 500, f"FATAL: DB has {final_count} tickets after insert, expected 500"
    print(f"[OK] Database now has exactly 500 tickets (400 old + {NEW_COUNT} new)")

    final_old_ids = set(doc["_id"] for doc in coll.find({"_id": {"$in": list(expected_old_ids)}}, {"_id": 1}))
    assert final_old_ids == expected_old_ids, "FATAL: the original 400 IDs changed after insert! This should be impossible with insert_many."
    print("[OK] Confirmed: the original 400 tickets are still exactly intact, untouched")

    new_in_db = set(doc["_id"] for doc in coll.find({"_id": {"$in": ids}}, {"_id": 1}))
    assert new_in_db == set(ids), "FATAL: new 100 IDs not all present in DB after insert"
    new_valid_count = coll.count_documents({"_id": {"$in": ids}, "status": "valid"})
    assert new_valid_count == NEW_COUNT, f"FATAL: only {new_valid_count}/{NEW_COUNT} new tickets are status='valid'"
    print(f"[OK] Confirmed: all {NEW_COUNT} new tickets present and status='valid'")

    all_ids_in_db = set(doc["_id"] for doc in coll.find({}, {"_id": 1}))
    assert all_ids_in_db == (expected_old_ids | set(ids)), "FATAL: final DB ID set doesn't exactly equal old+new — something unexpected is in there"
    print("[OK] Confirmed: DB ID set == exactly (old 400) union (new 100), nothing extra, nothing missing")

    client.close()

    pdf_path = os.path.join(OUTPUT_DIR, f"gayatri_maharas_tickets_batch2_{NEW_COUNT}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"\nSaved new batch PDF: {pdf_path}")

    csv_path = os.path.join(OUTPUT_DIR, f"tickets_batch2_{NEW_COUNT}_backup.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["seq", "short_code", "ticket_id", "status"])
        writer.writeheader()
        for r in sorted(rows, key=lambda r: r["seq"]):
            writer.writerow({"seq": r["seq"], "short_code": r["short_code"], "ticket_id": r["ticket_id"], "status": r["status"]})
    print(f"Saved new batch backup CSV: {csv_path}")

    print("\n=== Confirming the 3 protected files were never touched ===")
    for p in PROTECTED_FILES:
        mtime_after = os.path.getmtime(p)
        assert mtime_after == mtimes_before[p], f"FATAL: protected file was modified! {p}"
        print(f"  [OK] unchanged: {os.path.basename(p)}")

    print(f"\n=== DONE. 500 total tickets (400 original + {NEW_COUNT} new) are live in the database. ===")
    print("The original 400 sold tickets remain fully valid and scannable. Nothing has been printed from this new batch yet.")


if __name__ == "__main__":
    main()
