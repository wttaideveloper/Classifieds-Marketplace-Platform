"""Format checks for tax registration numbers (GSTIN, EU / UK VAT numbers) and exemption certificate references.

These checks catch typos and made-up numbers; they do not prove the business is registered. That needs the tax authority's
register (the GST portal, VIES), so a number that passes here is still approved by a person before it changes any tax.
"""
import re
from dataclasses import dataclass

GSTIN_RE = re.compile(r"^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$")
_GST_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
    "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}

# Country prefix -> pattern of what follows it. Greece uses the prefix EL.
VAT_PATTERNS = {
    "AT": r"U\d{8}", "BE": r"[01]\d{9}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]", "CZ": r"\d{8,10}", "DE": r"\d{9}", "DK": r"\d{8}",
    "EE": r"\d{9}", "EL": r"\d{9}", "ES": r"[A-Z0-9]\d{7}[A-Z0-9]", "FI": r"\d{8}", "FR": r"[A-Z0-9]{2}\d{9}", "HR": r"\d{11}",
    "HU": r"\d{8}", "IE": r"(\d[A-Z0-9+*]\d{5}[A-Z]|\d{7}[A-W][A-I]?)", "IT": r"\d{11}", "LT": r"(\d{9}|\d{12})", "LU": r"\d{8}",
    "LV": r"\d{11}", "MT": r"\d{8}", "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}", "RO": r"\d{2,10}", "SE": r"\d{12}",
    "SI": r"\d{8}", "SK": r"\d{10}", "GB": r"(\d{9}|\d{12}|GD\d{3}|HA\d{3})",
}
COUNTRY_TO_VAT_PREFIX = {"GR": "EL"}


@dataclass
class TaxIdCheck:
    valid: bool
    normalized: str
    message: str
    state: str | None = None          # the state a GSTIN belongs to
    checksum_ok: bool | None = None


def normalize(number: str) -> str:
    return re.sub(r"[\s\-.]", "", (number or "").upper())


def gstin_checksum(first14: str) -> str:
    """The 15th character of a GSTIN, from the first fourteen (mod-36 weighted sum)."""
    total = 0
    for index, char in enumerate(first14):
        value = _GST_ALPHABET.index(char) * (1 if index % 2 == 0 else 2)
        total += value // 36 + value % 36
    return _GST_ALPHABET[(36 - total % 36) % 36]


def check_gstin(number: str) -> TaxIdCheck:
    value = normalize(number)
    match = GSTIN_RE.match(value)
    if not match:
        return TaxIdCheck(False, value, "A GSTIN has 15 characters: 2 digits (state), the 10-character PAN, a digit or letter, Z, and a check character")
    state = GST_STATE_CODES.get(match.group(1))
    if state is None:
        return TaxIdCheck(False, value, f"{match.group(1)} is not a GST state code")
    ok = gstin_checksum(value[:14]) == value[14]
    if not ok:
        return TaxIdCheck(False, value, "The check character of this GSTIN is wrong. Re-check the number", state=state, checksum_ok=False)
    return TaxIdCheck(True, value, "The GSTIN has a valid format", state=state, checksum_ok=True)


def check_vat(number: str, country: str | None = None) -> TaxIdCheck:
    value = normalize(number)
    prefix = value[:2]
    if prefix not in VAT_PATTERNS:
        return TaxIdCheck(False, value, "Start the VAT number with the two-letter country code, for example DE123456789")
    expected = COUNTRY_TO_VAT_PREFIX.get((country or "").upper(), (country or "").upper())
    if country and expected != prefix:
        return TaxIdCheck(False, value, f"This VAT number is for {prefix}, not {country.upper()}")
    if not re.fullmatch(VAT_PATTERNS[prefix], value[2:]):
        return TaxIdCheck(False, value, f"This does not look like a {prefix} VAT number")
    return TaxIdCheck(True, value, "The VAT number has a valid format")


def check_certificate(number: str) -> TaxIdCheck:
    value = (number or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-/ ._]{3,59}", value):
        return TaxIdCheck(False, value, "A certificate reference has 4 to 60 letters, digits or - / . _")
    return TaxIdCheck(True, value, "The reference has a valid format. A person checks the certificate itself")


def check_tax_id(id_type: str, number: str, country: str | None = None) -> TaxIdCheck:
    if id_type == "gstin":
        return check_gstin(number)
    if id_type == "vat":
        return check_vat(number, country)
    return check_certificate(number)
