"""Column-name heuristics for personal data. Flagged columns trigger human approval."""

import re

_PII = re.compile(
    r"(e_?mail|phone|mobile|ssn|social_?sec|passport|national_?id|tax_?id|aadhaar|pan_?number"
    r"|address|street|postal|zip_?code|birth|^dob$|first_?name|last_?name|full_?name|surname"
    r"|ip_?addr|credit_?card|card_?number|iban|account_?number|salary|password|secret|token)",
    re.IGNORECASE,
)


def is_pii_column(name: str) -> bool:
    return bool(_PII.search(name))


def mask_value(value: object) -> object:
    return None if value is None else "***"
