"""Tax rules (versioned), the calculation (GST split, VAT, sales tax, inclusive prices, rounding, exemption, reverse charge),
tax numbers and the merchant tax profile."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models.payment_model import FinanceAuditLog, TaxExemption, TaxRule
from tests.pay_support import TENANT_A, notices_of, world  # noqa: F401

API = "/tax"
GSTIN = "27AAPFU0939F1ZV"      # Maharashtra; a well-known sample with a valid check character


def rule(world, **extra):
    body = {"tax_type": "gst", "name": "GST", "country": "IN", "rate": "18", "applies_to": "all", **extra}
    resp = world.as_user(world.people.root).post(f"{API}/rules", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def india_gst(world):
    """CGST + SGST 9% each within a state, IGST 18% between states."""
    return [
        rule(world, name="CGST", rate="9", condition="intra_state", priority=10),
        rule(world, name="SGST", rate="9", condition="intra_state", priority=20),
        rule(world, name="IGST", rate="18", condition="inter_state", priority=30),
    ]


def profile(world, user=None, **extra):
    body = {"country": "IN", "state": "Karnataka", "is_tax_registered": True, **extra}
    resp = world.as_user(user or world.people.asha).put(f"{API}/merchant-profile", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def calc(world, *, country="IN", state="Karnataka", amount="1000", currency="INR", category="service", user=None, quantity=1,
         tax_id=None, is_business=False, include_tax=False, tenant=TENANT_A, items=None):
    body = {"currency": currency, "items": items or [{"description": "Consultation", "amount": amount, "quantity": quantity, "category": category}],
            "customer": {"country": country, "state": state, "tax_id": tax_id, "is_business": is_business},
            "merchant_tenant_id": str(tenant) if tenant else None, "prices_include_tax": include_tax}
    resp = world.as_user(user or world.people.priya).post(f"{API}/calculate", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- GST (India) --------------------------------------------------------------------------------------------------------

def test_a_sale_inside_one_state_is_split_into_cgst_and_sgst(world):
    india_gst(world)
    profile(world)

    out = calc(world, state="Karnataka")

    assert out["subtotal"] == "1000.00" and out["tax_total"] == "180.00" and out["total"] == "1180.00"
    assert [(b["name"], b["tax_amount"]) for b in out["breakdown"]] == [("CGST", "90.00"), ("SGST", "90.00")]
    assert out["lines"][0]["taxes"][0]["rate"] == "9.000000" and out["is_exempt"] is False and out["reverse_charge"] is False


def test_a_sale_between_states_is_igst(world):
    india_gst(world)
    profile(world)

    out = calc(world, state="Maharashtra")

    assert [(b["name"], b["tax_amount"]) for b in out["breakdown"]] == [("IGST", "180.00")] and out["total"] == "1180.00"


def test_the_state_comparison_ignores_case_and_spaces_around_the_name(world):
    india_gst(world)
    profile(world, state="  KARNATAKA ")

    assert [b["name"] for b in calc(world, state="karnataka")["breakdown"]] == ["CGST", "SGST"]


# --- VAT, sales tax, categories ---------------------------------------------------------------------------------------------

def test_vat_on_a_price_that_includes_it_is_split_back_into_net_and_tax(world):
    rule(world, tax_type="vat", name="VAT", country="GB", rate="20")
    profile(world, country="GB", state=None)

    out = calc(world, country="GB", state=None, amount="120", currency="EUR", include_tax=True)

    assert out["subtotal"] == "100.00" and out["tax_total"] == "20.00" and out["total"] == "120.00" and out["prices_include_tax"] is True


def test_an_inclusive_price_with_two_components_adds_up_exactly(world):
    rule(world, name="CGST", rate="9", condition="intra_state")
    rule(world, name="SGST", rate="9", condition="intra_state")
    profile(world)

    out = calc(world, amount="100.01", include_tax=True)

    parts = [Decimal(b["tax_amount"]) for b in out["breakdown"]]
    assert Decimal(out["subtotal"]) + sum(parts) == Decimal("100.01") and Decimal(out["total"]) == Decimal("100.01")


def test_a_state_sales_tax_applies_to_products_in_that_state_only(world):
    rule(world, tax_type="sales_tax", name="CA sales tax", country="US", state="California", rate="7.25", applies_to="product")
    profile(world, country="US", state="California")

    assert calc(world, country="US", state="California", category="product", currency="USD")["tax_total"] == "72.50"
    assert calc(world, country="US", state="California", category="service", currency="USD")["tax_total"] == "0.00"      # services are not taxed
    assert calc(world, country="US", state="Texas", category="product", currency="USD")["tax_total"] == "0.00"


def test_a_service_tax_rule_works_like_any_other(world):
    rule(world, tax_type="service_tax", name="Service tax", rate="15", applies_to="service")
    profile(world)

    out = calc(world, quantity=3, amount="200")

    assert out["subtotal"] == "600.00" and out["tax_total"] == "90.00" and out["lines"][0]["quantity"] == 3


def test_no_rule_for_the_country_means_no_tax_and_a_warning(world):
    profile(world)

    out = calc(world, country="FR", state=None)

    assert out["tax_total"] == "0.00" and "no_tax_rule_for_FR" in out["warnings"]


def test_a_missing_merchant_profile_is_flagged(world):
    rule(world, rate="18")

    assert "supplier_tax_profile_missing" in calc(world)["warnings"]


def test_a_merchant_who_is_not_registered_charges_no_tax(world):
    rule(world, rate="18")
    profile(world, is_tax_registered=False)

    out = calc(world)

    assert out["tax_total"] == "0.00" and "not registered" in out["note"]


# --- rounding and currencies -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,expected", [("half_up", "3"), ("half_even", "2"), ("up", "3"), ("down", "2")])
def test_each_rule_rounds_with_its_own_mode_to_the_currencys_decimals(world, mode, expected):
    rule(world, name="Tax", rate="10", rounding_mode=mode)
    profile(world)

    out = calc(world, amount="25", currency="JPY")                 # 10% of 25 yen is 2.5

    assert out["tax_total"] == expected and "." not in out["total"]


def test_three_decimal_currencies_keep_three_decimals(world):
    rule(world, name="Tax", rate="5")
    profile(world)

    assert calc(world, amount="10.333", currency="KWD")["tax_total"] == "0.517"


def test_unsupported_currencies_are_refused(world):
    resp = world.as_user(world.people.priya).post(f"{API}/calculate", json={
        "currency": "GBP", "items": [{"amount": "1"}], "customer": {"country": "IN"}})

    assert resp.status_code == 422


# --- exemption and reverse charge -----------------------------------------------------------------------------------------------

def register(world, user, **extra):
    body = {"id_type": "exemption_certificate", "number": "CERT-2026-001", "country": "IN", **extra}
    return world.as_user(user).post(f"{API}/exemptions", json=body)


def approve(world, exemption_id):
    return world.as_user(world.people.root).patch(f"{API}/exemptions/{exemption_id}", json={"action": "approve"})


def test_an_exemption_certificate_only_counts_once_a_super_admin_approves_it(world):
    rule(world, rate="18")
    profile(world)
    priya = world.people.priya
    made = register(world, priya)
    assert made.status_code == 201 and made.json()["status"] == "pending" and made.json()["format_valid"] is True

    assert calc(world, user=priya)["tax_total"] == "180.00"                                          # pending: still charged
    assert approve(world, made.json()["id"]).json()["status"] == "validated"
    out = calc(world, user=priya)
    assert out["tax_total"] == "0.00" and out["is_exempt"] is True and "CERT-2026-001" in out["exempt_reason"]
    assert calc(world, user=world.people.ravi)["tax_total"] == "180.00"                              # someone else's certificate is theirs


def test_an_expired_or_other_country_or_other_state_certificate_does_not_apply(world):
    rule(world, rate="18")
    profile(world)
    priya = world.people.priya
    made = register(world, priya, state="Kerala").json()
    approve(world, made["id"])

    assert calc(world, user=priya, state="Karnataka")["is_exempt"] is False                          # a Kerala certificate
    assert calc(world, user=priya, state="Kerala")["is_exempt"] is True
    world.db.query(TaxExemption).update({"valid_until": (datetime.utcnow() - timedelta(days=1)).date()})
    world.db.commit()
    assert calc(world, user=priya, state="Kerala")["is_exempt"] is False


def test_a_revoked_certificate_stops_applying(world):
    rule(world, rate="18")
    profile(world)
    made = register(world, world.people.priya).json()
    approve(world, made["id"])

    revoked = world.as_user(world.people.root).patch(f"{API}/exemptions/{made['id']}", json={"action": "revoke", "note": "Certificate expired"})

    assert revoked.json()["status"] == "revoked" and calc(world, user=world.people.priya)["is_exempt"] is False


def test_reverse_charge_applies_to_an_approved_business_abroad_and_not_to_others(world):
    rule(world, tax_type="vat", name="VAT", country="DE", rate="19", reverse_charge_allowed=True)
    profile(world)                                                                                   # the merchant supplies from India
    priya = world.people.priya
    made = world.as_user(priya).post(f"{API}/exemptions", json={"id_type": "vat", "number": "DE 123 456 789", "country": "DE", "legal_name": "Priya GmbH"})
    assert made.status_code == 201 and made.json()["number"] == "DE123456789"

    charged = calc(world, user=priya, country="DE", state=None, is_business=True, tax_id="DE123456789")
    assert charged["tax_total"] == "190.00" and "customer_tax_id_not_approved" in charged["warnings"]       # not approved yet
    approve(world, made.json()["id"])
    reverse = calc(world, user=priya, country="DE", state=None, is_business=True, tax_id="DE123456789")
    assert reverse["tax_total"] == "0.00" and reverse["reverse_charge"] is True and "Reverse charge" in reverse["note"]
    assert reverse["lines"][0]["taxes"][0]["reverse_charge"] is True and reverse["breakdown"][0]["tax_amount"] == "0.00"
    consumer = calc(world, user=priya, country="DE", state=None, is_business=False)
    assert consumer["tax_total"] == "190.00" and consumer["reverse_charge"] is False                       # a consumer is charged


def test_reverse_charge_needs_the_supplier_to_be_in_another_country(world):
    rule(world, tax_type="vat", name="VAT", country="DE", rate="19", reverse_charge_allowed=True)
    profile(world, country="DE", state=None)                                                          # a domestic sale
    made = world.as_user(world.people.priya).post(f"{API}/exemptions", json={"id_type": "vat", "number": "DE123456789", "country": "DE"}).json()
    approve(world, made["id"])

    out = calc(world, user=world.people.priya, country="DE", state=None, is_business=True)

    assert out["tax_total"] == "190.00" and out["reverse_charge"] is False


def test_auto_approval_can_be_switched_on_for_valid_numbers(world, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TAX_ID_AUTO_APPROVE_VALID_FORMAT", True)

    made = world.as_user(world.people.priya).post(f"{API}/exemptions", json={"id_type": "vat", "number": "DE123456789", "country": "DE"}).json()
    cert = register(world, world.people.priya).json()

    assert made["status"] == "validated" and cert["status"] == "pending"                              # a certificate is always checked by a person


# --- tax numbers ---------------------------------------------------------------------------------------------------------------

def validate(world, **body):
    return world.as_user(world.people.priya).post(f"{API}/validate-exemption", json=body)


def test_a_gstin_is_checked_for_format_and_check_character(world):
    good = validate(world, id_type="gstin", number=GSTIN.lower()).json()
    assert good["valid"] is True and good["checksum_ok"] is True and good["state"] == "Maharashtra" and good["number"] == GSTIN
    bad = validate(world, id_type="gstin", number="27AAPFU0939F1ZX").json()
    assert bad["valid"] is False and bad["checksum_ok"] is False and "check character" in bad["message"]
    assert validate(world, id_type="gstin", number="12345").json()["valid"] is False
    assert validate(world, id_type="gstin", number="99XXXXX0000X1Z1").json()["valid"] is False


def test_vat_numbers_are_checked_against_the_country(world):
    assert validate(world, id_type="vat", number="DE123456789", country="DE").json()["valid"] is True
    assert validate(world, id_type="vat", number="NL123456789B01", country="NL").json()["valid"] is True
    assert validate(world, id_type="vat", number="EL123456789", country="GR").json()["valid"] is True
    wrong_country = validate(world, id_type="vat", number="DE123456789", country="FR").json()
    assert wrong_country["valid"] is False and "not FR" in wrong_country["message"]
    assert validate(world, id_type="vat", number="DE12345", country="DE").json()["valid"] is False
    assert validate(world, id_type="vat", number="123456789").json()["valid"] is False


def test_a_certificate_reference_has_a_simple_format(world):
    assert validate(world, id_type="exemption_certificate", number="AB-1234/2026").json()["valid"] is True
    assert validate(world, id_type="exemption_certificate", number="a").json()["valid"] is False


def test_registering_a_wrong_number_or_the_same_number_twice_is_refused(world):
    priya = world.people.priya

    assert world.as_user(priya).post(f"{API}/exemptions", json={"id_type": "gstin", "number": "27AAPFU0939F1ZX", "country": "IN"}).status_code == 422
    assert world.as_user(priya).post(f"{API}/exemptions", json={"id_type": "gstin", "number": GSTIN, "country": "DE"}).status_code == 422
    made = world.as_user(priya).post(f"{API}/exemptions", json={"id_type": "gstin", "number": GSTIN, "country": "IN"})
    assert made.status_code == 201 and made.json()["state"] == "Maharashtra"                          # the state comes from the number
    assert world.as_user(priya).post(f"{API}/exemptions", json={"id_type": "gstin", "number": GSTIN, "country": "IN"}).status_code == 409


def test_only_a_super_admin_decides_and_rejecting_needs_a_note(world):
    made = register(world, world.people.priya).json()

    assert world.as_user(world.people.asha).patch(f"{API}/exemptions/{made['id']}", json={"action": "approve"}).status_code == 403
    assert world.as_user(world.people.priya).patch(f"{API}/exemptions/{made['id']}", json={"action": "approve"}).status_code == 403
    assert world.as_user(world.people.root).patch(f"{API}/exemptions/{made['id']}", json={"action": "reject"}).status_code == 422
    rejected = world.as_user(world.people.root).patch(f"{API}/exemptions/{made['id']}", json={"action": "reject", "note": "Certificate is unreadable"})
    assert rejected.json()["status"] == "rejected" and rejected.json()["review_note"] == "Certificate is unreadable"
    assert world.as_user(world.people.root).patch(f"{API}/exemptions/{made['id']}", json={"action": "approve"}).status_code == 409
    assert world.db.query(FinanceAuditLog).filter_by(action="tax_registration_rejected").count() == 1


def test_people_see_their_own_numbers_and_the_super_admin_sees_all(world):
    register(world, world.people.priya)
    register(world, world.people.ravi, number="CERT-OTHER-9")

    assert world.as_user(world.people.priya).get(f"{API}/exemptions").json()["total"] == 1
    assert world.as_user(world.people.root).get(f"{API}/exemptions").json()["total"] == 2
    assert world.as_user(world.people.root).get(f"{API}/exemptions", params={"user_id": world.people.ravi["id"]}).json()["total"] == 1
    assert world.as_user(world.people.root).get(f"{API}/exemptions", params={"status": "validated"}).json()["total"] == 0


# --- rules: versions, audit, access -----------------------------------------------------------------------------------------------

def test_changing_a_rule_adds_a_version_and_the_old_one_still_explains_earlier_dates(world):
    created = rule(world, name="VAT", tax_type="vat", country="GB", rate="20", valid_from=(datetime.utcnow() - timedelta(days=30)).isoformat())
    root = world.people.root

    changed = world.as_user(root).put(f"{API}/rules/{created['id']}", json={"rate": "22", "reason": "Budget change"})

    assert changed.status_code == 200 and changed.json()["version"] == 2 and changed.json()["rate"] == "22.000000"
    assert changed.json()["supersedes_id"] == created["id"] and changed.json()["rule_key"] == created["rule_key"] and changed.json()["is_current"] is True
    versions = world.as_user(root).get(f"{API}/rules/{created['id']}/versions").json()["items"]
    assert [v["version"] for v in versions] == [2, 1] and versions[1]["valid_to"] is not None and versions[1]["is_current"] is False
    current = world.as_user(world.people.priya).get(f"{API}/rules", params={"country": "GB"}).json()["items"]
    assert [(r["version"], r["rate"]) for r in current] == [(2, "22.000000")]
    # the date decides which version applies
    from app.services import tax_service

    long_ago = tax_service.rules_in_force(world.db, "GB", datetime.utcnow() - timedelta(days=10))
    assert [r.version for r in long_ago] == [1]
    entry = world.db.query(FinanceAuditLog).filter_by(action="tax_rule_changed").one()
    assert entry.reason == "Budget change" and Decimal(entry.before["rate"]) == 20 and Decimal(entry.after["rate"]) == 22
    assert world.as_user(root).put(f"{API}/rules/{created['id']}", json={"rate": "25"}).status_code == 409        # only the latest version changes


def test_a_future_dated_change_starts_later(world):
    created = rule(world, name="VAT", tax_type="vat", country="GB", rate="20")
    soon = (datetime.utcnow() + timedelta(days=5)).isoformat()

    new = world.as_user(world.people.root).put(f"{API}/rules/{created['id']}", json={"rate": "21", "valid_from": soon}).json()

    assert new["is_current"] is False
    now_rules = world.as_user(world.people.priya).get(f"{API}/rules", params={"country": "GB"}).json()["items"]
    assert [(r["version"], r["rate"]) for r in now_rules] == [(1, "20.000000")]
    assert world.as_user(world.people.priya).get(f"{API}/rules", params={"country": "GB", "current_only": "false"}).json()["total"] == 2


def test_a_change_must_change_something_and_not_start_in_the_past(world):
    created = rule(world, rate="18", valid_from=datetime.utcnow().isoformat())
    root = world.people.root

    assert world.as_user(root).put(f"{API}/rules/{created['id']}", json={}).status_code == 422
    assert world.as_user(root).put(f"{API}/rules/{created['id']}", json={"rate": "5", "valid_from": (datetime.utcnow() - timedelta(days=3)).isoformat()}).status_code == 422


def test_a_rule_can_be_switched_off_and_stops_applying(world):
    created = rule(world, rate="18")
    profile(world)

    off = world.as_user(world.people.root).delete(f"{API}/rules/{created['id']}", params={"reason": "No longer applies"})

    assert off.status_code == 200 and off.json()["is_active"] is False
    assert calc(world)["tax_total"] == "0.00"
    assert world.as_user(world.people.root).delete(f"{API}/rules/{created['id']}").status_code == 409


def test_rules_are_validated(world):
    root = world.people.root
    base = {"tax_type": "vat", "name": "VAT", "country": "GB", "rate": "20"}

    assert world.as_user(root).post(f"{API}/rules", json={**base, "rate": "101"}).status_code == 422
    assert world.as_user(root).post(f"{API}/rules", json={**base, "country": "ZZ"}).status_code == 422
    assert world.as_user(root).post(f"{API}/rules", json={**base, "tax_type": "toll"}).status_code == 422
    assert world.as_user(root).post(f"{API}/rules", json={**base, "rounding_mode": "banker"}).status_code == 422
    assert world.as_user(root).post(f"{API}/rules", json={**base, "valid_from": "2026-05-01T00:00:00", "valid_to": "2026-04-01T00:00:00"}).status_code == 422


def test_only_the_super_admin_changes_rules_and_a_customer_can_read_them(world):
    created = rule(world)

    for user in (world.people.asha, world.people.priya):
        assert world.as_user(user).post(f"{API}/rules", json={"tax_type": "gst", "name": "X", "country": "IN", "rate": "1"}).status_code == 403
        assert world.as_user(user).put(f"{API}/rules/{created['id']}", json={"rate": "1"}).status_code == 403
        assert world.as_user(user).delete(f"{API}/rules/{created['id']}").status_code == 403
        assert world.as_user(user).get(f"{API}/rules/{created['id']}/versions").status_code == 403
    assert world.as_user(world.people.priya).get(f"{API}/rules").json()["total"] == 1
    assert world.as_user(None).get(f"{API}/rules").status_code == 401
    assert world.as_user(None).post(f"{API}/calculate", json={"currency": "INR", "items": [{"amount": "1"}], "customer": {"country": "IN"}}).status_code == 401


def test_a_rule_change_is_audited_and_the_super_admins_are_told(world):
    created = rule(world, name="IGST")
    world.as_user(world.people.root).put(f"{API}/rules/{created['id']}", json={"rate": "12"})
    world.as_user(world.people.root).delete(f"{API}/rules/{created['id']}")

    assert [e.action for e in world.db.query(FinanceAuditLog).order_by(FinanceAuditLog.created_at).all()] == ["tax_rule_created", "tax_rule_changed", "tax_rule_deactivated"]
    notices = notices_of(world, "tax_rule_updated")
    assert len(notices) == 3 and {str(u) for u in notices[0]["user_ids"]} == {world.people.root["id"], world.people.root2["id"]}
    assert notices[1]["metadata"]["action"] == "changed" and notices[1]["metadata"]["rate"] == "12.000000" and "Platform Root" in notices[1]["message"]


# --- merchant tax profile --------------------------------------------------------------------------------------------------------------

def test_a_merchant_saves_and_reads_only_their_own_tax_profile(world):
    saved = profile(world, tax_id=GSTIN.lower(), legal_name="Calm Clinic Pvt Ltd")

    assert saved["tax_id"] == GSTIN and saved["tax_id_type"] == "gstin" and saved["tenant_id"] == str(TENANT_A)
    assert world.as_user(world.people.asha).get(f"{API}/merchant-profile").json()["state"] == "Karnataka"
    assert world.as_user(world.people.bela).get(f"{API}/merchant-profile").status_code == 404                  # theirs is separate
    assert world.as_user(world.people.asha).get(f"{API}/merchant-profile", params={"tenant_id": str(TENANT_A)}).status_code == 200
    other = world.as_user(world.people.bela).get(f"{API}/merchant-profile", params={"tenant_id": str(TENANT_A)})
    assert other.status_code == 403
    assert world.as_user(world.people.priya).get(f"{API}/merchant-profile").status_code == 403
    assert world.as_user(world.people.root).get(f"{API}/merchant-profile").status_code == 422                   # must say which business
    assert world.as_user(world.people.root).get(f"{API}/merchant-profile", params={"tenant_id": str(TENANT_A)}).json()["country"] == "IN"


def test_the_merchant_tax_id_is_checked(world):
    bad = world.as_user(world.people.asha).put(f"{API}/merchant-profile", json={"country": "IN", "tax_id": "27AAPFU0939F1ZX"})

    assert bad.status_code == 422 and "check character" in bad.json()["detail"]
    assert world.db.query(FinanceAuditLog).filter_by(action="merchant_tax_profile_saved").count() == 0
