"""Orders API for the online shop.

Lists products and places orders. Stock is held in memory for now, so each
pod has its own separate copy. Redis replaces this with shared stock later.
"""

import socket

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="orders")

# The pod's name. Returned in responses so you can see which pod answered.
POD_NAME = socket.gethostname()

STOCK = {"widget": 100, "gadget": 50, "gizmo": 20}


class Order(BaseModel):
    product: str
    quantity: int = 1


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/products")
def list_products():
    return {"products": STOCK, "served_by": POD_NAME}


@app.post("/orders", status_code=201)
def place_order(order: Order):
    if order.product not in STOCK:
        raise HTTPException(status_code=404, detail=f"Unknown product: {order.product}")
    if order.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1")
    if STOCK[order.product] < order.quantity:
        raise HTTPException(status_code=409, detail="Not enough stock")

    STOCK[order.product] -= order.quantity
    return {
        "product": order.product,
        "quantity": order.quantity,
        "remaining_stock": STOCK[order.product],
        "served_by": POD_NAME,
    }
