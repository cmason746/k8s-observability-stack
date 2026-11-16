"""Orders API for the online shop.

Lists products and places orders. Stock is owned by the inventory service,
which orders calls over HTTP using its Kubernetes Service name.

Settings come from environment variables, which Kubernetes fills in from the
orders ConfigMap:
  LOG_LEVEL      How much the app logs (debug, info, warning, error)
  INVENTORY_URL  Base URL of the inventory service, for example http://inventory
"""

import logging
import os
import socket

import httpx
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter
from pydantic import BaseModel

import http_metrics

# --- Settings, read once when the app starts ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("orders")
# httpx logs every call to inventory at INFO, which drowns out the app's own lines
logging.getLogger("httpx").setLevel(logging.WARNING)

# Business metric: successful orders per product. Only counted after inventory
# accepts the order, so made-up product names can't add new label values.
ORDERS_PLACED = Counter("orders_placed_total", "Orders placed successfully", ["product"])

# The pod's name. Returned in responses so you can see which pod answered.
POD_NAME = socket.gethostname()

# One shared HTTP client, reusing connections. A short timeout so a slow
# inventory service doesn't hang orders requests.
inventory = httpx.Client(base_url=INVENTORY_URL, timeout=2.0)

app = FastAPI(title="orders")
http_metrics.setup(app)  # RED metrics on /metrics, and quieter access logs
log.info("Using inventory at %s", INVENTORY_URL)


class Order(BaseModel):
    product: str
    quantity: int = 1


def call_inventory(method: str, path: str, **kwargs) -> httpx.Response:
    """Call inventory, turning connection problems and server errors into a 503."""
    try:
        response = inventory.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        log.error("Inventory unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="Inventory unavailable")
    if response.status_code >= 500:
        log.error("Inventory returned %d", response.status_code)
        raise HTTPException(status_code=503, detail="Inventory unavailable")
    return response


@app.get("/health")
def health():
    """Liveness and readiness. Deliberately doesn't check inventory, so an
    inventory problem doesn't take orders out of service too."""
    return {"status": "ok"}


@app.get("/products")
def list_products():
    log.debug("Listing products")
    stock = call_inventory("GET", "/stock").json()["stock"]
    return {"products": stock, "served_by": POD_NAME}


@app.post("/orders", status_code=201)
def place_order(order: Order):
    if order.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1")

    response = call_inventory("POST", "/reserve", json=order.model_dump())
    if response.status_code != 200:
        # Pass inventory's 404 (unknown product) or 409 (not enough stock) straight back
        detail = response.json().get("detail")
        log.warning("Order rejected: %s x%d: %s", order.product, order.quantity, detail)
        raise HTTPException(status_code=response.status_code, detail=detail)

    reserved = response.json()
    ORDERS_PLACED.labels(order.product).inc()  # One per order, whatever the quantity
    log.info("Order placed: %s x%d, %d left", order.product, order.quantity, reserved["remaining_stock"])
    return {
        "product": order.product,
        "quantity": order.quantity,
        "remaining_stock": reserved["remaining_stock"],
        "served_by": POD_NAME,
    }
