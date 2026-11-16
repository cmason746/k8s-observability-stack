"""Load generator for the online shop.

Sends a steady, realistic mix of requests to the orders service, including
some that fail on purpose, so the dashboards have real traffic to show.
Logs a summary of response codes every SUMMARY_SECONDS.

Settings come from environment variables (the loadgen ConfigMap):
  TARGET_URL           Orders base URL, for example http://orders
  REQUESTS_PER_SECOND  Average request rate
  PRODUCTS             Comma-separated products to order
  WEIGHT_BROWSE        Relative share of GET /products
  WEIGHT_ORDER         Relative share of valid orders
  WEIGHT_UNKNOWN       Relative share of orders for a product that doesn't exist (404)
  WEIGHT_INVALID       Relative share of orders with quantity 0 (400)
  SUMMARY_SECONDS      How often to log a summary
"""

import logging
import os
import random
import signal
import sys
import time
from collections import Counter

import httpx

TARGET_URL = os.getenv("TARGET_URL", "http://orders")
RATE = float(os.getenv("REQUESTS_PER_SECOND", "2"))
PRODUCTS = os.getenv("PRODUCTS", "widget,gadget,gizmo").split(",")
SUMMARY_SECONDS = int(os.getenv("SUMMARY_SECONDS", "30"))
ACTIONS = ["browse", "order", "unknown", "invalid"]
WEIGHTS = [float(os.getenv(f"WEIGHT_{a.upper()}", w)) for a, w in zip(ACTIONS, ["60", "30", "7", "3"])]

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("loadgen")
# httpx logs every request at INFO, which drowns out the summaries
logging.getLogger("httpx").setLevel(logging.WARNING)

# As the container's main process, Python ignores the stop signal Kubernetes
# sends unless it handles it, so the pod would take 30s to shut down
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

# No keep-alive: every request opens a new connection, like separate users would.
# A Service picks a pod once per connection, so one reused connection would send
# all the traffic to a single orders pod.
client = httpx.Client(
    base_url=TARGET_URL,
    timeout=5.0,
    limits=httpx.Limits(max_keepalive_connections=0),
)


def send(action: str) -> httpx.Response:
    if action == "browse":
        return client.get("/products")
    if action == "order":
        return client.post("/orders", json={"product": random.choice(PRODUCTS), "quantity": random.randint(1, 3)})
    if action == "unknown":
        return client.post("/orders", json={"product": "doohickey", "quantity": 1})
    return client.post("/orders", json={"product": random.choice(PRODUCTS), "quantity": 0})


def main():
    log.info("Sending ~%.1f requests/s to %s, mix %s", RATE, TARGET_URL, dict(zip(ACTIONS, WEIGHTS)))
    counts = Counter()
    last_summary = time.monotonic()

    while True:
        action = random.choices(ACTIONS, weights=WEIGHTS)[0]
        try:
            counts[send(action).status_code] += 1
        except httpx.HTTPError as exc:
            counts[type(exc).__name__] += 1

        if time.monotonic() - last_summary >= SUMMARY_SECONDS:
            log.info("Last %ds: %s", SUMMARY_SECONDS, dict(sorted(counts.items(), key=str)))
            counts.clear()
            last_summary = time.monotonic()

        # Random gaps that average out to RATE, like real users arriving
        time.sleep(random.expovariate(RATE))


if __name__ == "__main__":
    main()
