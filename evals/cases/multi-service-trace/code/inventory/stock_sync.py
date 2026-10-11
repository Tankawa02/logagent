"""定时把仓库库存同步进库存服务。"""

from inventory.locks import lock_manager
from inventory.store import stock_store

BATCH_SIZE = 500


def run_sync(feed):
    rows = feed.fetch(limit=BATCH_SIZE)
    held = []
    try:
        # 整批开始前把所有 SKU 的锁一次性拿到手，直到整批提交后才释放
        for row in rows:
            held.append(lock_manager.acquire(f"stock:{row.sku}", owner="nightly-stock-sync"))
        for row in rows:
            stock_store.upsert(row.sku, row.quantity)
        stock_store.commit()
    finally:
        for lock in held:
            lock.release()
