"""report-job：每月生成区域销售报表。

调度平台按 crontab 启动，环境变量见 deploy/report-job.cron.env。
"""

import sys

import settings as settings_module
from aggregate import aggregate
from warehouse import connect, fetch_orders
from writer import write_report

VERSION = "3.4.1"
SETTINGS_PATH = "/etc/report-job/settings.toml"


def _period() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    from datetime import date

    today = date.today()
    return f"{today.year}-{today.month:02d}"


def main() -> None:
    print(f"report-job v{VERSION}")
    print(f"loading settings from {SETTINGS_PATH}")
    settings = settings_module.load(SETTINGS_PATH)
    print(f"settings: output_dir={settings['output_dir']} format={settings['format']}")
    db = connect("analytics")
    period = _period()
    print(f"fetching orders for period {period}")
    orders = fetch_orders(db, period)
    print(f"fetched {len(orders)} orders")
    rows = aggregate(orders, settings)
    write_report(rows, settings)


if __name__ == "__main__":
    main()
