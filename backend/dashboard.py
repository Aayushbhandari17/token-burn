import asyncio
import json
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse
from redis.asyncio import Redis

router = APIRouter()
redis_client = Redis(host='localhost', port=6379, db=0)

@router.get("/dashboard/stream")
async def dashboard_stream(request: Request):
    """
    Server-Sent Events endpoint forwarding Redis Pub/Sub events to the dashboard.
    """
    async def event_generator():
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("events")
        try:
            while True:
                if await request.is_disconnected():
                    break
                    
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg:
                    # 'data' should be a JSON string from the publisher
                    data = msg["data"].decode("utf-8") if isinstance(msg["data"], bytes) else msg["data"]
                    yield {"data": data}
                else:
                    await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe("events")
            await pubsub.close()

    return EventSourceResponse(event_generator())
