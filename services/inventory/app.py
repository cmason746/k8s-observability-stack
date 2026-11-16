"""Inventory service for the online shop.

Owns the stock, which is kept in Redis so every inventory pod (and every
orders pod calling them) sees the same numbers.

Settings come from environment variables:
  LOG_LEVEL       How much the app logs (debug, info, warning, error)
  INITIAL_STOCK   Starting stock as JSON, for example {"widget": 100}
  REDIS_HOST      Redis hostname (the Redis Service name)
  REDIS_PORT      Redis port
  REDIS_PASSWORD  Redis password, from the redis-auth Secret
"""

import json
import logging
import os
import socket

import redis
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# --- Settings, read once when the app starts ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()
INITIAL_STOCK = json.loads(os.getenv("INITIAL_STOCK", '{"widget": 100, "gadget": 50, "gizmo": 20}'))
STOCK_KEY = "stock"  # A Redis hash: product -> quantity

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("inventory")

POD_NAME = socket.gethostname()

# Short timeouts, so a missing Redis fails fast instead of hanging requests and probes
db = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
    socket_timeout=1,
    socket_connect_timeout=1,
)

# Reserve stock in one step inside Redis, so two orders can't both take the last item.
# Returns the remaining stock, -1 for an unknown product, or -2 for not enough stock.
RESERVE = db.register_script("""
local current = redis.call('HGET', KEYS[1], ARGV[1])
if not current then return -1 end
if tonumber(current) < tonumber(ARGV[2]) then return -2 end
return redis.call('HINCRBY', KEYS[1], ARGV[1], -tonumber(ARGV[2]))
""")

app = FastAPI(title="inventory")


def seed_stock():
    """Add the starting stock for any product not already in Redis.

    HSETNX only sets a field if it's missing, so this is safe to run any number
    of times, and it refills Redis after a Redis restart.
    """
    for product, quantity in INITIAL_STOCK.items():
        if db.hsetnx(STOCK_KEY, product, quantity):
            log.info("Seeded %s with %d", product, quantity)


@app.exception_handler(redis.RedisError)
def redis_down(request, exc):
    log.error("Redis error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "Stock store unavailable"})


class Reservation(BaseModel):
    product: str
    quantity: int = 1


@app.get("/health")
def health():
    """Liveness: the process is up and answering. Deliberately doesn't touch Redis."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: Redis is reachable and the stock is seeded."""
    try:
        db.ping()
        seed_stock()
    except redis.RedisError as exc:
        log.warning("Not ready: %s", exc)
        raise HTTPException(status_code=503, detail="Redis unavailable")
    return {"status": "ready"}


@app.get("/stock")
def list_stock():
    stock = {product: int(qty) for product, qty in db.hgetall(STOCK_KEY).items()}
    return {"stock": stock, "served_by": POD_NAME}


@app.post("/reserve")
def reserve(item: Reservation):
    if item.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1")

    remaining = RESERVE(keys=[STOCK_KEY], args=[item.product, item.quantity])
    if remaining == -1:
        log.warning("Reserve for unknown product %s", item.product)
        raise HTTPException(status_code=404, detail=f"Unknown product: {item.product}")
    if remaining == -2:
        log.warning("Not enough stock for %s x%d", item.product, item.quantity)
        raise HTTPException(status_code=409, detail="Not enough stock")

    log.info("Reserved %s x%d, %d left", item.product, item.quantity, remaining)
    return {
        "product": item.product,
        "quantity": item.quantity,
        "remaining_stock": remaining,
        "served_by": POD_NAME,
    }
