"""Orders API for the online shop.

Lists products and places orders. Stock is held in memory for now, so each
pod has its own separate copy. Redis replaces this with shared stock later.

Settings come from environment variables, which Kubernetes fills in from the
orders ConfigMap:
  LOG_LEVEL      How much the app logs (debug, info, warning, error)
  INITIAL_STOCK  Starting stock as JSON, for example {"widget": 100}
"""

import json
import logging
import os
import socket

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# --- Settings, read once when the app starts ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()
DEFAULT_STOCK = {"widget": 100, "gadget": 50, "gizmo": 20}
STOCK = json.loads(os.environ["INITIAL_STOCK"]) if "INITIAL_STOCK" in os.environ else dict(DEFAULT_STOCK)

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("orders")

# The pod's name. Returned in responses so you can see which pod answered.
POD_NAME = socket.gethostname()

app = FastAPI(title="orders")
log.info("Starting with stock %s", STOCK)


class Order(BaseModel):
    product: str
    quantity: int = 1


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/products")
def list_products():
    log.debug("Listing products")
    return {"products": STOCK, "served_by": POD_NAME}


@app.post("/orders", status_code=201)
def place_order(order: Order):
    if order.product not in STOCK:
        log.warning("Order for unknown product %s", order.product)
        raise HTTPException(status_code=404, detail=f"Unknown product: {order.product}")
    if order.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1")
    if STOCK[order.product] < order.quantity:
        log.warning("Not enough stock for %s x%d", order.product, order.quantity)
        raise HTTPException(status_code=409, detail="Not enough stock")

    STOCK[order.product] -= order.quantity
    log.info("Order placed: %s x%d, %d left", order.product, order.quantity, STOCK[order.product])
    return {
        "product": order.product,
        "quantity": order.quantity,
        "remaining_stock": STOCK[order.product],
        "served_by": POD_NAME,
    }
