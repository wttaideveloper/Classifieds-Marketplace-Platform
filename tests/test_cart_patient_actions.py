from datetime import datetime, timedelta
from uuid import UUID
from app.api.v1.endpoints.cart import router
from app.core.dependencies import get_current_user
from app.models.cart_model import OrderStatusEvent
from app.models.enterprise_model import Enterprise
from app.models.product_model import Product
from tests.pay_support import world, TENANT_A

def test_checkout_cancel_and_admin_boundary(world, monkeypatch):
    db = world.db
    OrderStatusEvent.__table__.create(db.get_bind(), checkfirst=True)
    world.app.include_router(router, prefix="/cart")
    monkeypatch.setattr("app.services.order_service._notify", lambda *a, **kw: None)
    user = world.people.priya
    world.app.dependency_overrides[get_current_user] = lambda: user
    ent = db.query(Enterprise).filter_by(tenant_id=TENANT_A).one()
    product = Product(tenant_id=TENANT_A, enterprise_id=ent.id, product_name="Mat", product_category="Fitness", product_price=10, stock_quantity=3, status="active", currency="INR")
    db.add(product); db.commit()
    client = world.as_user(user)
    r = client.post("/cart/", json={"product_id":str(product.id), "quantity":2})
    assert r.status_code == 201, r.text
    address = dict(full_name="Jane Doe", phone="12345678", line1="12 Test Way", city="Pune", state="MH", zip="411001", country="IN")
    r = client.post("/cart/checkout", json={"shipping_address":address})
    assert r.status_code in (200,201), r.text
    path="/cart/orders/"+r.json()["id"]
    db.refresh(product)
    assert product.stock_quantity == 1
    assert client.get(path).json()["can_cancel"]
    assert client.patch(path+"/status",json={"status":"confirmed"}).status_code == 403
    assert client.post(path+"/refund/approve",json={"action":"approve"}).status_code == 403
    assert world.as_user(world.people.bela).patch(path+"/status",json={"status":"confirmed"}).status_code == 404
    r = world.as_user(user).post(path+"/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert client.post(path+"/cancel").status_code == 409
    db.refresh(product)
    assert product.stock_quantity == 3

def test_paid_order_needs_store_decision_and_refund_window(world, monkeypatch):
    from tests.test_pay_refunds import paid
    db=world.db
    OrderStatusEvent.__table__.create(db.get_bind(), checkfirst=True)
    world.app.include_router(router,prefix="/cart")
    monkeypatch.setattr("app.services.order_service._notify",lambda *a,**kw:None)
    user=world.people.priya
    world.app.dependency_overrides[get_current_user]=lambda:user
    order,payment=paid(world)
    order.status="confirmed";order.delivered_at=None
    order.shipping_phone="12345678";order.shipping_line1="12 Test Way";order.shipping_city="Pune";order.shipping_zip="411001"
    db.commit()
    path="/cart/orders/"+str(order.id)
    r=world.as_user(user).post(path+"/cancel",json={"reason":"Ordered by mistake"})
    assert r.status_code==200,r.text
    assert r.json()["status"]=="cancellation_requested"
    r=world.as_user(world.people.asha).post(path+"/cancel/decision",json={"action":"reject","reason":"Already packed"})
    assert r.status_code==200,r.text
    assert r.json()["status"]=="confirmed"
    order.status="delivered";order.delivered_at=datetime.utcnow()-timedelta(days=8);db.commit()
    client=world.as_user(user)
    assert not client.get(path).json()["can_refund"]
    r=client.post("/refunds",json={"payment_id":payment["id"],"reason":"Wrong item"})
    assert r.status_code==409 and "7-day" in r.text
    order.delivered_at=datetime.utcnow()-timedelta(days=6);db.commit()
    assert client.get(path).json()["can_refund"]
    r=client.post("/refunds",json={"payment_id":payment["id"],"reason":"Wrong item"})
    assert r.status_code==201,r.text
    assert client.get(path).json()["refund_id"]==r.json()["id"]
    assert client.post(path+"/refund/approve",json={"action":"approve"}).status_code==403
    admin=world.as_user(world.people.asha)
    approved=admin.post(path+"/refund/approve",json={"action":"approve"})
    assert approved.status_code==200,approved.text
    assert approved.json()["id"]==r.json()["id"] and approved.json()["status"]=="completed"
    assert admin.post(path+"/refund/approve",json={"action":"approve"}).status_code==409
def test_store_approval_restocks_paid_order_once_without_claiming_refund(world, monkeypatch):
    from tests.test_pay_refunds import paid
    db=world.db
    OrderStatusEvent.__table__.create(db.get_bind(), checkfirst=True)
    world.app.include_router(router,prefix="/cart")
    monkeypatch.setattr("app.services.order_service._notify",lambda *a,**kw:None)
    user=world.people.priya
    world.app.dependency_overrides[get_current_user]=lambda:user
    order,payment=paid(world)
    ent=db.query(Enterprise).filter_by(tenant_id=TENANT_A).one()
    line=order.items[0]
    product=Product(id=line.product_id,tenant_id=TENANT_A,enterprise_id=ent.id,product_name="Mat",product_category="Fitness",product_price=10,stock_quantity=2)
    db.add(product)
    order.stock_reduced=True;line.stock_debited=line.quantity
    order.status="confirmed";order.delivered_at=None
    order.shipping_phone="12345678";order.shipping_line1="12 Test Way";order.shipping_city="Pune";order.shipping_zip="411001"
    db.commit()
    path="/cart/orders/"+str(order.id)
    r=world.as_user(user).post(path+"/cancel",json={"reason":"Changed plans"})
    assert r.status_code==200,r.text
    db.refresh(product)
    assert product.stock_quantity==2
    assert world.as_user(world.people.bela).post(path+"/cancel/decision",json={"action":"approve"}).status_code==404
    client=world.as_user(world.people.asha)
    r=client.post(path+"/cancel/decision",json={"action":"approve"})
    assert r.status_code==200,r.text
    body=r.json()
    assert body["status"]=="cancelled" and body["refund_due"] and body["refund_id"] is None
    assert client.post(path+"/cancel/decision",json={"action":"approve"}).status_code==409
    db.refresh(product)
    assert product.stock_quantity==3


def test_a_cart_holds_one_business_and_checkout_refuses_a_mixed_one(world, monkeypatch):
    from app.models.cart_model import CartItem
    from tests.pay_support import TENANT_B

    db = world.db
    OrderStatusEvent.__table__.create(db.get_bind(), checkfirst=True)
    world.app.include_router(router, prefix="/cart")
    user = world.people.priya
    world.app.dependency_overrides[get_current_user] = lambda: user
    ent_a = db.query(Enterprise).filter_by(tenant_id=TENANT_A).one()
    ent_b = db.query(Enterprise).filter_by(tenant_id=TENANT_B).one()
    mat = Product(tenant_id=TENANT_A, enterprise_id=ent_a.id, product_name="Mat", product_category="Fitness", product_price=10, stock_quantity=5, status="active", currency="INR")
    strap = Product(tenant_id=None, enterprise_id=ent_a.id, product_name="Strap", product_category="Fitness", product_price=5, stock_quantity=5, status="active", currency="INR")   # tenant via its enterprise
    other = Product(tenant_id=TENANT_B, enterprise_id=ent_b.id, product_name="Band", product_category="Fitness", product_price=7, stock_quantity=5, status="active", currency="INR")
    db.add_all([mat, strap, other])
    db.commit()
    client = world.as_user(user)

    assert client.post("/cart/", json={"product_id": str(mat.id), "quantity": 1}).status_code == 201
    assert client.post("/cart/", json={"product_id": str(strap.id), "quantity": 1}).status_code == 201          # same business, found through its enterprise
    refused = client.post("/cart/", json={"product_id": str(other.id), "quantity": 1})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "CART_OTHER_BUSINESS"

    from app.models.cart_model import Cart

    mine = db.query(Cart).filter_by(user_id=UUID(str(user["id"]))).first()
    db.add(CartItem(cart_id=mine.id, product_id=other.id, quantity=1, unit_price=7, currency="INR"))     # a cart that mixed before the rule existed
    db.commit()
    address = dict(full_name="Jane Doe", phone="12345678", line1="12 Test Way", city="Pune", state="MH", zip="411001", country="IN")
    mixed = client.post("/cart/checkout", json={"shipping_address": address})
    assert mixed.status_code == 409 and mixed.json()["detail"]["code"] == "CART_MIXED_BUSINESSES"
