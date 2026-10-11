"""下单时预占库存。"""

from inventory.locks import lock_manager
from inventory.store import stock_store

LOCK_WAIT_SECONDS = 30


def reserve(sku: str, qty: int, request_id: str):
    with lock_manager.acquire(f"stock:{sku}", owner=request_id, timeout=LOCK_WAIT_SECONDS):
        available = stock_store.get(sku)
        if available < qty:
            raise ValueError(f"insufficient stock for {sku}")
        return stock_store.reserve(sku, qty, request_id)
