from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings

_client = AsyncIOMotorClient(settings.mongodb_uri)
db = _client[settings.mongodb_db]

users_coll = db["users"]
tickets_coll = db["tickets"]
