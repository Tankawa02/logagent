"""按区域汇总订单。"""

from collections import defaultdict

REGION_GROUPS = {
    "cn-east": ["shanghai", "hangzhou", "nanjing"],
    "cn-south": ["shenzhen", "guangzhou"],
    "cn-north": ["beijing", "tianjin"],
}


def aggregate(orders, settings):
    print("aggregating by region")
    totals = defaultdict(float)
    for order in orders:
        totals[order.city] += order.amount
    # settings 里必须有 region：配置文件没写时依赖环境变量 REPORT_JOB_REGION
    regions = REGION_GROUPS[settings["region"]]
    return [(city, totals[city]) for city in regions]
