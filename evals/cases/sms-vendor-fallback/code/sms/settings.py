"""短信通道配置。2026-08 起 Twilio 合同到期已下线，海外流量统一走 Infobip。"""

COUNTRY_VENDORS = {
    "CN": ["aliyun"],
    "HK": ["aliyun", "infobip"],
    "SG": ["infobip", "aliyun"],
    "MY": ["infobip"],
    "TH": ["infobip"],
}

# 没有单独配置的国家按这个顺序尝试
DEFAULT_CHAIN = ["infobip", "twilio"]

# 各通道支持的国家；None 表示不限
VENDOR_COUNTRIES = {
    "aliyun": {"CN", "HK", "SG"},
    "infobip": {"HK", "SG", "MY", "TH", "PH", "VN"},
    "twilio": None,
}
