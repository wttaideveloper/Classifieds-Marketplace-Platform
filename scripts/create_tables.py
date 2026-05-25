from app.db.database import Base, engine

# Import models so SQLAlchemy metadata includes them
import app.models.address_model  # noqa: F401
import app.models.admin_model  # noqa: F401
import app.models.blog_model  # noqa: F401
import app.models.category_model  # noqa: F401
import app.models.customer_model  # noqa: F401
import app.models.merchant_model  # noqa: F401
import app.models.review_model  # noqa: F401
import app.models.review_moderation_history_model  # noqa: F401
import app.models.order_model  # noqa: F401
import app.models.notification_model  # noqa: F401
import app.models.push_notification_model  # noqa: F401
import app.models.moderation_model  # noqa: F401

if __name__ == '__main__':
    Base.metadata.create_all(bind=engine)
    print('Tables created (or already exist).')
