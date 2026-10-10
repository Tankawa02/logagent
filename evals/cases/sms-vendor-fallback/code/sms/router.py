import logging

from . import settings

log = logging.getLogger("sms.router")

_PREFIXES = {"+86": "CN", "+852": "HK", "+65": "SG", "+60": "MY", "+66": "TH", "+62": "ID", "+63": "PH", "+84": "VN"}


class NoVendorError(RuntimeError):
    pass


def country_of(phone: str) -> str:
    for prefix in sorted(_PREFIXES, key=len, reverse=True):
        if phone.startswith(prefix):
            return _PREFIXES[prefix]
    return "UNKNOWN"


def candidates(country: str) -> list[str]:
    return settings.COUNTRY_VENDORS.get(country, settings.DEFAULT_CHAIN)


def supports(vendor: str, country: str) -> bool:
    allowed = settings.VENDOR_COUNTRIES.get(vendor)
    return allowed is None or country in allowed


def pick_vendor(phone: str) -> str:
    country = country_of(phone)
    for vendor in candidates(country):
        if supports(vendor, country):
            return vendor
        log.debug("vendor %s does not support %s", vendor, country)
    raise NoVendorError(country)
