"""导出接口：GET /api/export，流式返回 CSV。"""

from db import pool


def export_orders(request, response):
    conn = pool.getconn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE created_at >= %s", (request.args["since"],))
    for row in cursor:
        response.write(",".join(map(str, row)) + "\n")
    pool.putconn(conn)
