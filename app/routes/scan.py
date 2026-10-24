from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import get_current_user, require_role
from app.config import settings
from app.db import tickets_coll
from app.schemas import CodeRequest

router = APIRouter(prefix="/api/scan", tags=["scan"], dependencies=[Depends(require_role("volunteer", "admin"))])

EVENT_TZ = ZoneInfo("Asia/Kolkata")


def _today_is_event_day() -> bool:
    today = datetime.now(EVENT_TZ).date().isoformat()
    return today == settings.event_date


def _lookup_filter(code: str) -> dict:
    """A scanned QR yields the full UUID; a volunteer typing a fallback code
    by hand will only have the short 8-char code printed on the ticket."""
    code = code.strip()
    if len(code) == 36 and code.count("-") == 4:
        return {"_id": code}
    return {"short_code": code.upper()}


def _require_scan_access(user):
    if user["role"] == "volunteer" and not _today_is_event_day():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Scanning only opens on {settings.event_date}",
        )


@router.get("/status")
async def scan_window_status(user=Depends(get_current_user)):
    scanning_open = True if user["role"] == "admin" else _today_is_event_day()
    return {
        "event_date": settings.event_date,
        "today": datetime.now(EVENT_TZ).date().isoformat(),
        "scanning_open": scanning_open,
    }


@router.post("/verify")
async def verify(body: CodeRequest, user=Depends(get_current_user)):
    """Read-only lookup — does NOT change ticket status. The volunteer/admin
    reviews this before separately confirming entry via /checkin."""
    _require_scan_access(user)

    ticket = await tickets_coll.find_one(_lookup_filter(body.code))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return {
        "ticket_id": ticket["ticket_id"],
        "short_code": ticket["short_code"],
        "status": ticket["status"],
        "used_at": ticket.get("used_at"),
        "scanned_by": ticket.get("scanned_by"),
        "cancelled_at": ticket.get("cancelled_at"),
    }


@router.post("/checkin")
async def checkin(body: CodeRequest, user=Depends(get_current_user)):
    """Atomically flips a ticket valid -> used. Called only after the
    volunteer/admin has reviewed the /verify result and explicitly confirmed."""
    _require_scan_access(user)

    result = await tickets_coll.find_one_and_update(
        {**_lookup_filter(body.code), "status": "valid"},
        {"$set": {
            "status": "used",
            "used_at": datetime.now(timezone.utc).isoformat(),
            "scanned_by": user["username"],
        }},
        return_document=True,
    )

    if result:
        return {"ok": True, "reason": "admitted", "short_code": result["short_code"]}

    existing = await tickets_coll.find_one(_lookup_filter(body.code))
    if not existing:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if existing["status"] == "cancelled":
        raise HTTPException(status_code=409, detail="This ticket was cancelled by the organizer")

    raise HTTPException(
        status_code=409,
        detail=f"Already used at {existing.get('used_at')} by {existing.get('scanned_by')}",
    )


@router.get("/stats")
async def stats():
    total = await tickets_coll.count_documents({})
    used = await tickets_coll.count_documents({"status": "used"})
    cancelled = await tickets_coll.count_documents({"status": "cancelled"})
    return {"total": total, "used": used, "cancelled": cancelled, "remaining": total - used - cancelled}
