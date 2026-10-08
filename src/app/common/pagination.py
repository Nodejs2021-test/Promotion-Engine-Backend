"""Page / page-size query parameters and paginated MongoDB queries."""

from typing import Annotated

from fastapi import Query

from app.common.utils import serialize

Page = Annotated[int, Query(ge=1)]

PageSize = Annotated[int, Query(ge=1, le=500)]


async def paginate(
    collection, query: dict, sort: list, page: int, page_size: int, projection: dict | None = None
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 500)
    total = await collection.count_documents(query)
    cursor = collection.find(query, projection).sort(sort).skip((page - 1) * page_size).limit(page_size)
    docs = [serialize(d) async for d in cursor]
    return {"items": docs, "total": total, "page": page, "page_size": page_size}
