"""Start-up hooks of the catalog service."""
from mskit.auth import tenant_lookup


def _local_tenant_lookup(db, enterprise_id):
    """Catalog answers enterprise -> tenant from its own database (other services call its internal API)."""
    from catalog_service.db.database import SessionLocal
    from catalog_service.models.enterprise_model import Enterprise

    own = db is None
    session = SessionLocal() if own else db
    try:
        ent = session.query(Enterprise).filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False)).first()
        return str(ent.tenant_id) if ent and ent.tenant_id else None
    finally:
        if own:
            session.close()


def register_lookup():
    tenant_lookup.register_local_lookup(_local_tenant_lookup)


register_lookup()  # also at import, so tests that never run the lifespan use the local lookup
HOOKS = [register_lookup]


# ---------------------------------------------------------------- reference data replication (owner side)
from mskit.replication import Publisher  # noqa: E402

from catalog_service.models.enterprise_model import Enterprise  # noqa: E402
from catalog_service.models.location_model import EnterpriseLocation  # noqa: E402
from catalog_service.models.product_model import Product  # noqa: E402
from catalog_service.models.service_model import Service  # noqa: E402

# Subscribers: REPLICA_SUBSCRIBERS=events,learning,booking,... (services that hold a read-only copy)
PUBLISHER = Publisher({"catalog.enterprises": Enterprise, "catalog.enterprise_locations": EnterpriseLocation,
                       "catalog.products": Product, "catalog.services": Service})  # services: read by booking (D4)  # products: read by commerce (cart prices, stock display)
PUBLISHER.install()


def _start_sweeper():
    from catalog_service.stock_api import start_sweeper

    start_sweeper()


HOOKS.append(_start_sweeper)  # releases stock reservations commerce never confirmed (D1)
