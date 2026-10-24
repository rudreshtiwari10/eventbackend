"""
Production ticket run: generate N tickets, verify EVERY SINGLE ONE before
touching the database, then insert into the real MongoDB Atlas cluster.

This never writes to the database unless every check before it passed. If
any check fails, it prints exactly what failed and exits without touching
MongoDB, so it's always safe to re-run after wiping the collection.
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import fitz  # PyMuPDF
from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode
from pymongo import MongoClient

from app.pdf_gen import build_tickets
from app.config import settings

COUNT = 400
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "production_output")


def main():
    print(f"=== Generating {COUNT} tickets ===")
    rows, pdf_bytes = build_tickets(COUNT)
    print(f"Generated {len(rows)} ticket records, PDF is {len(pdf_bytes) / 1024 / 1024:.2f} MB")

    # --- Check 1: exact count ---
    assert len(rows) == COUNT, f"FATAL: expected {COUNT} rows, got {len(rows)}"

    # --- Check 2: every UUID is unique ---
    ids = [r["ticket_id"] for r in rows]
    assert len(set(ids)) == COUNT, f"FATAL: duplicate UUIDs found! {COUNT - len(set(ids))} collisions"
    print(f"[OK] All {COUNT} UUIDs are unique")

    # --- Check 3: every short_code is unique (used for manual fallback lookup) ---
    codes = [r["short_code"] for r in rows]
    assert len(set(codes)) == COUNT, f"FATAL: duplicate short codes found! {COUNT - len(set(codes))} collisions"
    print(f"[OK] All {COUNT} short codes are unique")

    # --- Check 4: PDF page count matches ---
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) == COUNT, f"FATAL: PDF has {len(doc)} pages, expected {COUNT}"
    print(f"[OK] PDF has exactly {COUNT} pages")

    # --- Check 5: EVERY QR code decodes to EXACTLY the intended UUID, in order ---
    print("Decoding all QR codes to verify each one round-trips correctly (this takes a bit)...")
    mismatches = []
    for i, row in enumerate(rows):
        page = doc[i]
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        results = zbar_decode(img)
        if not results:
            mismatches.append((i, row["ticket_id"], "NO QR DETECTED"))
            continue
        decoded = results[0].data.decode("utf-8")
        if decoded != row["ticket_id"]:
            mismatches.append((i, row["ticket_id"], decoded))

    if mismatches:
        print(f"FATAL: {len(mismatches)} QR mismatches found:")
        for m in mismatches:
            print(" ", m)
        print("ABORTING — nothing was written to the database.")
        sys.exit(1)

    print(f"[OK] All {COUNT} QR codes decode to exactly their intended UUID")

    # --- Check 6: all rows start life as status="valid" ---
    assert all(r["status"] == "valid" for r in rows), "FATAL: some rows are not status='valid'"
    print(f"[OK] All {COUNT} rows are status='valid'")

    print("\n=== All checks passed. Writing to MongoDB Atlas. ===")
    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15000)
    client.admin.command("ping")
    db = client[settings.mongodb_db]
    coll = db["tickets"]

    existing = coll.count_documents({})
    if existing:
        print(f"Collection currently has {existing} document(s). Wiping before this run...")
        deleted = coll.delete_many({}).deleted_count
        print(f"Deleted {deleted} existing document(s).")

    result = coll.insert_many(rows, ordered=True)
    print(f"Inserted {len(result.inserted_ids)} documents.")

    # --- Post-write verification: read back and cross-check ---
    db_count = coll.count_documents({})
    assert db_count == COUNT, f"FATAL: DB has {db_count} docs after insert, expected {COUNT}"
    valid_count = coll.count_documents({"status": "valid"})
    assert valid_count == COUNT, f"FATAL: DB has {valid_count} valid docs, expected {COUNT}"

    db_ids = set(doc["_id"] for doc in coll.find({}, {"_id": 1}))
    assert db_ids == set(ids), "FATAL: DB ticket_id set does not match generated set"

    print(f"[OK] Verified: DB now has exactly {COUNT} tickets, all status='valid', IDs match generated set exactly")
    client.close()

    # --- Save outputs ---
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUTPUT_DIR, f"gayatri_maharas_tickets_{COUNT}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"\nSaved printable PDF: {pdf_path}")

    import csv
    csv_path = os.path.join(OUTPUT_DIR, f"tickets_{COUNT}_backup.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["seq", "short_code", "ticket_id", "status"])
        writer.writeheader()
        for r in sorted(rows, key=lambda r: r["seq"]):
            writer.writerow({"seq": r["seq"], "short_code": r["short_code"], "ticket_id": r["ticket_id"], "status": r["status"]})
    print(f"Saved backup CSV (door master list source): {csv_path}")

    print("\n=== DONE. Nothing has been printed yet — review the PDF before printing. ===")


if __name__ == "__main__":
    main()
