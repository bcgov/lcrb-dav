from pymongo import MongoClient, ASCENDING
from api.core.config import settings
from .collections import COLLECTION_NAMES


async def get_async_session():
    yield None


client = MongoClient(settings.MONGODB_URL, uuidRepresentation="standard")


async def init_db():
    # must be idempotent
    db = client[settings.DB_NAME]


async def get_db():
    return client[settings.DB_NAME]
