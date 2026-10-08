"""MongoDB connection (PyMongo native async API) and readable sequential ids."""

import asyncio
from decimal import Decimal

from bson.codec_options import CodecOptions, TypeCodec, TypeRegistry
from bson.decimal128 import Decimal128
from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.core.config import settings


class DecimalCodec(TypeCodec):
    """Store Python Decimal values as BSON Decimal128 so money is never a float in the database."""

    python_type = Decimal
    bson_type = Decimal128

    def transform_python(self, value):
        return Decimal128(value)

    def transform_bson(self, value):
        return value.to_decimal()


CODEC_OPTIONS = CodecOptions(type_registry=TypeRegistry([DecimalCodec()]))

_client: AsyncMongoClient | None = None

_client_loop: asyncio.AbstractEventLoop | None = None


def get_client() -> AsyncMongoClient:
    """Return a client bound to the running event loop (re-created if the loop changed, e.g. in tests)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        _client = AsyncMongoClient(
            settings.mongo_uri,
            tz_aware=False,
            appname=settings.mongo_app_name,
            maxPoolSize=settings.mongo_max_pool_size,
            serverSelectionTimeoutMS=settings.mongo_server_selection_timeout_ms,
        )
        _client_loop = loop
    return _client


def get_db() -> AsyncDatabase:
    return get_client().get_database(settings.db_name, codec_options=CODEC_OPTIONS)


async def close_client() -> None:
    global _client, _client_loop
    if _client is not None:
        await _client.close()
    _client = None
    _client_loop = None


async def check_connection() -> dict:
    """Ping MongoDB; raise a clear error if it cannot be reached."""
    try:
        await get_db().command("ping")
        info = await get_client().server_info()
    except PyMongoError as exc:
        raise RuntimeError(
            f"Cannot connect to MongoDB at {settings.mongo_uri} (database '{settings.db_name}'): {exc}. "
            "Start MongoDB or set PE_MONGO_URI."
        ) from exc
    return {"version": info.get("version"), "database": settings.db_name}


# Human-readable sequential identifiers (CAM-001, RULE-001, KEY-0001) backed by the counters collection.
async def next_id(prefix: str, width: int = 4) -> str:
    doc = await get_db().counters.find_one_and_update(
        {"_id": prefix}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    return f"{prefix}-{doc['seq']:0{width}d}"
