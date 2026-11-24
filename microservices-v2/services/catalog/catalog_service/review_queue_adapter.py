"""Catalog's part of the review moderation queue (product and service reviews), copied branch by branch from
app/services/review_admin_service.py (_scoped_query, delete_any_review, moderate_any_review)."""
from fastapi import HTTPException
from sqlalchemy.orm import joinedload

from catalog_service.services.review_access import can_view_all_tenants
from mskit.review_queue import Adapter


class CatalogReviews(Adapter):
    modules = ("product", "service")

    def scoped_query(self, db, module, access):
        from catalog_service.core.catalog_access import scope_catalog_query

        everyone = can_view_all_tenants(access)
        if module == "product":
            from catalog_service.models.product_model import Product as Item, ProductReview as Review

            fk, name = Review.product_id, Item.product_name
        else:
            from catalog_service.models.service_model import Service as Item, ServiceReview as Review

            fk, name = Review.service_id, Item.service_name
        q = db.query(Review, Item).join(Item, Item.id == fk).options(joinedload(Item.enterprise))
        if hasattr(Item, "is_deleted"):
            q = q.filter(Item.is_deleted.is_(False))
        if not everyone:
            q = scope_catalog_query(q, Item, access)
        return q, Review, Item, fk, name

    def moderate(self, db, module, review_id, action, access, *, access_token, actor, reason):
        if module == "product":
            from catalog_service.models.product_model import ProductReview
            from catalog_service.services.product_service import moderate_product_review_service

            review = db.get(ProductReview, review_id)
            if review is None:
                raise HTTPException(status_code=404, detail="Review not found")
            moderate_product_review_service(db, review.product_id, review_id, action, access, access_token=access_token, actor=actor, reason=reason)
        else:
            from catalog_service.models.service_model import ServiceReview
            from catalog_service.services.service_service import moderate_service_review_service

            review = db.get(ServiceReview, review_id)
            if review is None:
                raise HTTPException(status_code=404, detail="Review not found")
            moderate_service_review_service(db, review.service_id, review_id, action, access, access_token=access_token, actor=actor, reason=reason)

    def delete(self, db, module, review_id, current_user, *, access_token, reason):
        if module == "product":
            from catalog_service.models.product_model import ProductReview
            from catalog_service.services.product_service import delete_product_review_service

            review = db.get(ProductReview, review_id)
            if review is None:
                raise HTTPException(status_code=404, detail="Review not found")
            return delete_product_review_service(db, review.product_id, review_id, current_user, access_token=access_token, reason=reason)
        from catalog_service.models.service_model import ServiceReview
        from catalog_service.services.service_service import delete_service_review_service

        review = db.get(ServiceReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        return delete_service_review_service(db, review.service_id, review_id, current_user, access_token=access_token, reason=reason)
