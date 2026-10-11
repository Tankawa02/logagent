"""数据库连接池。"""

import os

from psycopg_pool import ConnectionPool

POOL_SIZE = 10
POOL_TIMEOUT_SECONDS = 3.0

pool = ConnectionPool(
    conninfo=f"postgresql://app:{os.environ['DB_PASSWORD']}@db-primary:5432/shop",
    max_size=POOL_SIZE,
    timeout=POOL_TIMEOUT_SECONDS,
)
