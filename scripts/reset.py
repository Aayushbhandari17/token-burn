import sqlite3
import asyncio
from redis.asyncio import Redis
import os

async def main():
    try:
        r = Redis(host='localhost', port=6379, db=0)
        await r.flushdb()
        print("Redis cleared.")
    except Exception as e:
        print(f"Redis error: {e}")
        
    db_path = "backend/data/requests.db"
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("DELETE FROM request_log")
                conn.commit()
            print("SQLite cleared.")
        except Exception as e:
            print(f"SQLite error: {e}")
    else:
        print("SQLite db not found, skipping.")

if __name__ == "__main__":
    asyncio.run(main())
