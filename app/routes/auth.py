from fastapi import APIRouter, HTTPException, status

from app.auth import create_access_token, verify_password
from app.db import users_coll
from app.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    user = await users_coll.find_one({"username": body.username})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    token = create_access_token(user["username"], user["role"])
    return LoginResponse(access_token=token, role=user["role"], username=user["username"])
