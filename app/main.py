from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import hash_password
from app.config import settings
from app.db import users_coll
from app.routes import admin, auth, scan


@asynccontextmanager
async def lifespan(app: FastAPI):
    existing_admin = await users_coll.find_one({"role": "admin"})
    if not existing_admin:
        await users_coll.insert_one({
            "username": settings.admin_username,
            "password_hash": hash_password(settings.admin_password),
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"Created initial admin account: {settings.admin_username}")
    yield


app = FastAPI(title="Gayatri Maharaas Ticketing", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Ticket-Count", "Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(scan.router)


@app.get("/api/health")
async def health():
    return {"ok": True, "event_date": settings.event_date}
