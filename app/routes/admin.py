from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.auth import hash_password, require_role
from app.db import tickets_coll, users_coll
from app.pdf_gen import build_tickets
from app.schemas import GenerateRequest, TicketOut, VolunteerCreateRequest

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_role("admin"))])


@router.post("/tickets/generate")
async def generate_tickets(body: GenerateRequest):
    rows, pdf_bytes = build_tickets(body.count)
    await tickets_coll.insert_many(rows)

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="tickets_{body.count}.pdf"',
            "X-Ticket-Count": str(body.count),
            "Access-Control-Expose-Headers": "X-Ticket-Count, Content-Disposition",
        },
    )


@router.get("/tickets", response_model=list[TicketOut])
async def list_tickets():
    cursor = tickets_coll.find({}).sort("seq", 1)
    out = []
    async for doc in cursor:
        out.append(TicketOut(
            ticket_id=doc["ticket_id"],
            short_code=doc["short_code"],
            status=doc["status"],
            used_at=doc.get("used_at"),
            scanned_by=doc.get("scanned_by"),
            cancelled_at=doc.get("cancelled_at"),
            seq=doc["seq"],
        ))
    return out


@router.post("/tickets/{ticket_id}/cancel")
async def cancel_ticket(ticket_id: str):
    result = await tickets_coll.find_one_and_update(
        {"_id": ticket_id, "status": "valid"},
        {"$set": {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()}},
    )
    if not result:
        existing = await tickets_coll.find_one({"_id": ticket_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Ticket not found")
        raise HTTPException(status_code=409, detail=f"Ticket is already '{existing['status']}', cannot cancel")
    return {"ok": True}


@router.post("/volunteers")
async def create_volunteer(body: VolunteerCreateRequest):
    existing = await users_coll.find_one({"username": body.username})
    if existing:
        raise HTTPException(status_code=409, detail="That username is already taken")

    await users_coll.insert_one({
        "username": body.username,
        "password_hash": hash_password(body.password),
        "role": "volunteer",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True}


@router.get("/volunteers")
async def list_volunteers():
    cursor = users_coll.find({"role": "volunteer"}, {"password_hash": 0})
    out = []
    async for doc in cursor:
        out.append({"username": doc["username"], "created_at": doc.get("created_at")})
    return out


@router.delete("/volunteers/{username}")
async def delete_volunteer(username: str):
    result = await users_coll.delete_one({"username": username, "role": "volunteer"})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Volunteer not found")
    return {"ok": True}
