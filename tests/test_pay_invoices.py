"""Invoices, credit notes and debit notes: numbering, contents, the PDF, e-mail, templates and who can see them."""
from decimal import Decimal
from uuid import UUID

import pytest

from app.models.payment_model import Invoice
from app.services import invoice_pdf
from tests.pay_support import TENANT_A, TENANT_B, cart_order, india_gst, notices_of, pay, post_rule, world  # noqa: F401

API = "/invoices"


def paid(world, user=None, tenant=TENANT_A, **extra):
    user = user or world.people.priya
    return pay(world, user, cart_order(world, user, tenant=tenant), **extra).json()


# --- numbering ------------------------------------------------------------------------------------------------------------------------------------

def test_invoice_numbers_run_in_an_unbroken_series_per_business(world):
    india_gst(world)
    post_rule(world, name="Other GST", rate="5", condition="any")
    first, second = paid(world), paid(world, world.people.ravi)
    other = paid(world, tenant=TENANT_B)

    numbers = {p["id"]: world.db.query(Invoice).filter_by(payment_id=UUID(p["id"])).one().number for p in (first, second, other)}

    year = __import__("datetime").datetime.utcnow().year
    assert numbers[first["id"]] == f"INV-{year}-000001" and numbers[second["id"]] == f"INV-{year}-000002"
    assert numbers[other["id"]] == f"INV-{year}-000001"                                  # another business has its own series


def test_an_invoice_is_issued_once_per_payment(world):
    india_gst(world)
    payment = paid(world)

    again = world.as_user(world.people.priya).post(f"{API}/generate", json={"payment_id": payment["id"]})

    assert again.status_code == 200 and world.db.query(Invoice).count() == 1
    assert again.json()["id"] == payment["invoice_id"]
    unpaid = pay(world, world.people.ravi, cart_order(world, world.people.ravi), token=None).json()["payment"]
    assert world.as_user(world.people.ravi).post(f"{API}/generate", json={"payment_id": unpaid["id"]}).status_code == 409
    assert world.as_user(world.people.ravi).post(f"{API}/generate", json={"payment_id": payment["id"]}).status_code == 404


# --- what is on it -------------------------------------------------------------------------------------------------------------------------------------

def test_the_invoice_shows_currency_exchange_rate_and_the_tax_breakdown(world):
    india_gst(world)
    world.as_user(world.people.asha).put("/tax/merchant-profile", json={"country": "IN", "state": "Karnataka", "tax_id": "27AAPFU0939F1ZV", "legal_name": "Calm Clinic Pvt Ltd",
                                                                         "address": "12 MG Road\nBengaluru"})
    payment = paid(world, currency="USD")

    invoice = world.as_user(world.people.priya).get(f"{API}/{payment['invoice_id']}").json()

    assert invoice["doc_type"] == "invoice" and invoice["status"] == "issued" and invoice["payment_id"] == payment["id"]
    assert invoice["currency"] == "USD" and invoice["item_currency"] == "INR" and invoice["exchange_rate"] == "0.0120000000"
    assert invoice["base_currency"] == "INR" and invoice["base_total"] == "1180.00"
    assert (invoice["subtotal"], invoice["tax_total"], invoice["total"]) == ("12.00", "2.16", "14.16")
    assert [(r["name"], r["rate"], r["tax_amount"]) for r in invoice["tax_breakdown"]] == [("CGST", "9.000000", "1.08"), ("SGST", "9.000000", "1.08")]
    line = invoice["lines"][0]
    assert line["description"] == "Yoga mat" and line["quantity"] == 1 and line["net"] == "12.00" and line["tax"] == "2.16" and line["gross"] == "14.16"
    assert invoice["supplier"]["name"] == "Calm Clinic Pvt Ltd" and invoice["supplier"]["tax_id"] == "27AAPFU0939F1ZV" and invoice["supplier"]["tax_id_label"] == "GSTIN"
    assert invoice["billing"]["country"] == "IN" and invoice["download_path"] == f"/api/v1/invoices/{invoice['id']}/download"


def test_an_invoice_keeps_what_it_said_when_the_rate_or_the_rule_changes_later(world):
    india_gst(world)
    payment = paid(world)
    rule = world.as_user(world.people.root).get("/tax/rules", params={"country": "IN"}).json()["items"][0]
    world.as_user(world.people.root).put(f"/tax/rules/{rule['id']}", json={"rate": "50"})
    world.as_user(world.people.asha).put("/tax/merchant-profile", json={"country": "IN", "state": "Kerala"})

    invoice = world.as_user(world.people.priya).get(f"{API}/{payment['invoice_id']}").json()

    assert invoice["total"] == "1180.00" and [r["tax_amount"] for r in invoice["tax_breakdown"]] == ["90.00", "90.00"]


# --- the PDF -----------------------------------------------------------------------------------------------------------------------------------------------

def pdf_text(content: bytes) -> str:
    fitz = pytest.importorskip("fitz")
    document = fitz.open(stream=content, filetype="pdf")
    return "\n".join(page.get_text() for page in document)


def test_the_pdf_download_carries_the_numbers(world):
    india_gst(world)
    world.as_user(world.people.asha).put("/tax/merchant-profile", json={"country": "IN", "state": "Karnataka", "tax_id": "27AAPFU0939F1ZV", "legal_name": "Calm Clinic Pvt Ltd"})
    payment = paid(world, currency="USD")

    resp = world.as_user(world.people.priya).get(f"{API}/{payment['invoice_id']}/download")

    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf" and resp.content.startswith(b"%PDF")
    number = world.db.query(Invoice).one().number
    assert resp.headers["content-disposition"] == f'attachment; filename="{number}.pdf"'
    text = pdf_text(resp.content)
    for expected in (number, "TAX INVOICE", "Calm Clinic Pvt Ltd", "GSTIN: 27AAPFU0939F1ZV", "Yoga mat", "CGST @ 9%", "SGST @ 9%", "USD 14.16", "USD 12.00",
                     "1 INR = 0.012000 USD", "Priya Customer"):
        assert expected in text, expected


def test_the_pdf_is_the_same_for_an_invoice_asked_twice_and_hides_other_peoples(world):
    india_gst(world)
    payment = paid(world)

    assert world.as_user(world.people.asha).get(f"{API}/{payment['invoice_id']}/download").status_code == 200
    assert world.as_user(world.people.root).get(f"{API}/{payment['invoice_id']}/download").status_code == 200
    assert world.as_user(world.people.ravi).get(f"{API}/{payment['invoice_id']}/download").status_code == 404
    assert world.as_user(world.people.bela).get(f"{API}/{payment['invoice_id']}/download").status_code == 404
    assert world.as_user(None).get(f"{API}/{payment['invoice_id']}/download").status_code == 401


def test_numbers_are_grouped_the_indian_way_for_en_in():
    assert invoice_pdf.group_number(Decimal("1234567.5"), 2, "en-IN") == "12,34,567.50"
    assert invoice_pdf.group_number(Decimal("1234567.5"), 2, "en-GB") == "1,234,567.50"
    assert invoice_pdf.group_number(Decimal("999"), 2, "en-IN") == "999.00"
    assert invoice_pdf.group_number(Decimal("1000"), 0, "en-IN") == "1,000"
    assert invoice_pdf.group_number(Decimal("-100000"), 2, "en-IN") == "-1,00,000.00"
    assert "?" in invoice_pdf.safe("Zoë 日本") and "<" not in invoice_pdf.safe("<b>x</b>")


# --- history -----------------------------------------------------------------------------------------------------------------------------------------------

def test_invoice_history_is_scoped_and_searchable(world):
    india_gst(world)
    mine = paid(world)
    paid(world, world.people.ravi)
    paid(world, tenant=TENANT_B)

    assert world.as_user(world.people.priya).get(API).json()["total"] == 2
    assert world.as_user(world.people.ravi).get(API).json()["total"] == 1
    assert world.as_user(world.people.asha).get(API).json()["total"] == 2
    assert world.as_user(world.people.root).get(API).json()["total"] == 3
    assert world.as_user(world.people.root).get(API, params={"tenant_id": str(TENANT_B)}).json()["total"] == 1
    number = world.as_user(world.people.priya).get(f"{API}/{mine['invoice_id']}").json()["number"]
    assert world.as_user(world.people.root).get(API, params={"search": number[-6:]}).json()["total"] == 2          # 000001 in two businesses series
    assert world.as_user(world.people.root).get(API, params={"search": "000002"}).json()["total"] == 1
    assert world.as_user(world.people.root).get(API, params={"doc_type": "credit_note"}).json()["total"] == 0
    assert world.as_user(world.people.root).get(API, params={"doc_type": "nonsense"}).status_code == 422
    assert world.as_user(world.people.ravi).get(f"{API}/{mine['invoice_id']}").status_code == 404


# --- e-mail ------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_invoice_is_announced_by_notification_and_can_be_e_mailed(world):
    india_gst(world)
    payment = paid(world)
    first = notices_of(world, "invoice_generated")
    assert len(first) == 1 and first[0]["channels"] == ["in_app", "push", "email"]                       # e-mailed straight away when issued
    assert first[0]["metadata"]["download_path"].endswith("/download") and first[0]["metadata"]["invoice_number"].startswith("INV-")

    sent = world.as_user(world.people.priya).post(f"{API}/{payment['invoice_id']}/email").json()
    again = world.as_user(world.people.asha).post(f"{API}/{payment['invoice_id']}/email").json()

    assert sent["email_count"] == 1 and sent["emailed_at"] and again["email_count"] == 2
    assert len(notices_of(world, "invoice_generated")) == 3
    assert world.as_user(world.people.ravi).post(f"{API}/{payment['invoice_id']}/email").status_code == 404


# --- debit notes -------------------------------------------------------------------------------------------------------------------------------------------

def test_a_business_raises_a_debit_note_against_an_invoice(world):
    india_gst(world)
    payment = paid(world)

    note = world.as_user(world.people.asha).post(f"{API}/{payment['invoice_id']}/debit-notes", json={"subtotal": "100", "tax_amount": "18", "reason": "Extra material used"})

    assert note.status_code == 201
    body = note.json()
    assert body["doc_type"] == "debit_note" and body["number"].startswith("DN-") and body["total"] == "118.00" and body["parent_invoice_id"] == payment["invoice_id"]
    assert body["reason"] == "Extra material used" and body["lines"][0]["description"].startswith("Adjustment to invoice INV-")
    assert world.as_user(world.people.priya).get(f"{API}/{body['id']}").status_code == 200                # the customer receives it
    assert world.as_user(world.people.priya).post(f"{API}/{payment['invoice_id']}/debit-notes", json={"subtotal": "1", "reason": "Because"}).status_code == 403
    assert world.as_user(world.people.bela).post(f"{API}/{payment['invoice_id']}/debit-notes", json={"subtotal": "1", "reason": "Because"}).status_code == 404
    assert world.as_user(world.people.asha).post(f"{API}/{payment['invoice_id']}/debit-notes", json={"subtotal": "0", "reason": "Nothing"}).status_code == 422
    assert world.as_user(world.people.asha).post(f"{API}/{body['id']}/debit-notes", json={"subtotal": "1", "reason": "On a note"}).status_code == 409
    assert world.as_user(world.people.asha).post(f"{API}/{payment['invoice_id']}/debit-notes", json={"subtotal": "1", "reason": "x"}).status_code == 422


# --- reverse charge and exemption on the paper -----------------------------------------------------------------------------------------------------

def test_a_reverse_charge_invoice_says_so(world):
    post_rule(world, tax_type="vat", name="VAT", country="DE", rate="19", reverse_charge_allowed=True)
    world.as_user(world.people.asha).put("/tax/merchant-profile", json={"country": "IN", "state": "Karnataka"})
    priya = world.people.priya
    registered = world.as_user(priya).post("/tax/exemptions", json={"id_type": "vat", "number": "DE123456789", "country": "DE"}).json()
    world.as_user(world.people.root).patch(f"/tax/exemptions/{registered['id']}", json={"action": "approve"})
    order = cart_order(world, priya)

    payment = pay(world, priya, order, country="DE", state=None, billing={"country": "DE", "tax_id": "DE123456789", "is_business": True}).json()

    assert payment["status"] == "succeeded" and payment["reverse_charge"] is True and payment["charge_tax"] == "0.00"
    invoice = world.as_user(priya).get(f"{API}/{payment['invoice_id']}").json()
    assert "Reverse charge" in invoice["notes"] and invoice["tax_total"] == "0.00" and invoice["total"] == "1000.00"


# --- templates -----------------------------------------------------------------------------------------------------------------------------------------------

def test_a_business_brands_its_invoices_and_old_invoices_do_not_change(world):
    india_gst(world)
    old = paid(world)
    asha = world.people.asha

    saved = world.as_user(asha).put(f"{API}/templates", json={"display_name": "Calm Clinic", "number_prefix": "bill", "accent_color": "#aa3355", "footer_text": "Thank you",
                                                               "terms": "Pay within 7 days", "locale": "en-GB"})

    assert saved.status_code == 200 and saved.json()["number_prefix"] == "BILL" and saved.json()["tenant_id"] == str(TENANT_A) and saved.json()["is_default"] is False
    new = paid(world, world.people.ravi)
    numbers = {p["id"]: world.db.query(Invoice).filter_by(payment_id=UUID(p["id"])).one().number for p in (old, new)}
    assert numbers[old["id"]].startswith("INV-") and numbers[new["id"]].startswith("BILL-")
    text = pdf_text(world.as_user(world.people.ravi).get(f"{API}/{new['invoice_id']}/download").content)
    assert "Calm Clinic" in text and "Thank you" in text and "Pay within 7 days" in text


def test_a_business_without_a_template_gets_the_platform_default_and_the_platform_sets_it(world):
    mine = world.as_user(world.people.asha).get(f"{API}/templates").json()
    assert mine["is_default"] is True and mine["number_prefix"] == "INV"
    world.as_user(world.people.root).put(f"{API}/templates", json={"footer_text": "Platform footer", "number_prefix": "TAX"})

    assert world.as_user(world.people.asha).get(f"{API}/templates").json()["footer_text"] == "Platform footer"
    assert world.as_user(world.people.bela).get(f"{API}/templates").json()["number_prefix"] == "TAX"
    world.as_user(world.people.asha).put(f"{API}/templates", json={"footer_text": "Our footer"})
    assert world.as_user(world.people.asha).get(f"{API}/templates").json()["footer_text"] == "Our footer"
    assert world.as_user(world.people.bela).get(f"{API}/templates").json()["footer_text"] == "Platform footer"          # businesses are separate
    assert world.as_user(world.people.root).get(f"{API}/templates", params={"tenant_id": str(TENANT_A)}).json()["footer_text"] == "Our footer"


def test_template_values_are_checked_and_only_businesses_use_them(world):
    asha = world.people.asha

    for bad in ({"number_prefix": "X"}, {"number_prefix": "CN"}, {"number_prefix": "inv-2"}, {"accent_color": "red"}, {"logo_url": "javascript:alert(1)"}):
        assert world.as_user(asha).put(f"{API}/templates", json=bad).status_code == 422, bad
    assert world.as_user(world.people.priya).get(f"{API}/templates").status_code == 403
    assert world.as_user(world.people.priya).put(f"{API}/templates", json={"footer_text": "x"}).status_code == 403
    assert world.as_user(asha).put(f"{API}/templates", params={"tenant_id": str(TENANT_B)}, json={"footer_text": "x"}).status_code == 403
