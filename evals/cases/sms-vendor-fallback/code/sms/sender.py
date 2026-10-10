import logging
import time

from .router import country_of, pick_vendor
from .vendors import client_for

log = logging.getLogger("sms.sender")


def send_code(biz: str, phone: str, code: str) -> bool:
    start = time.monotonic()
    vendor = pick_vendor(phone)
    result = client_for(vendor).send(phone, f"您的验证码是 {code}，5 分钟内有效")
    log.info(
        "sms sent biz=%s phone=%s country=%s vendor=%s status=%s cost=%dms",
        biz, phone, country_of(phone), vendor, result.status, (time.monotonic() - start) * 1000,
    )
    return result.ok
