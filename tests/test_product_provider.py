from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.product import router
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.models.product_model import Product


def test_product_provider_create_update_read_and_clear(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine,tables=[Enterprise.__table__,EnterpriseLocation.__table__,Product.__table__])
    sessions=sessionmaker(bind=engine)
    enterprise_id=uuid4(); provider_id=uuid4()
    with sessions() as db:
        db.add(Enterprise(id=enterprise_id,business_short_name='Test',business_legal_name='Test Ltd',business_email='test@example.com')); db.commit()
    monkeypatch.setattr('app.services.response_mappers.get_catalog_reviews',lambda *a,**kw:([],0))
    app=FastAPI(); app.include_router(router,prefix='/api/v1/products')
    def database():
        with sessions() as db: yield db
    app.dependency_overrides[get_db]=database
    with TestClient(app) as client:
        response=client.post('/api/v1/products/',json={'enterprise_id':str(enterprise_id),'product_name':'Product','category':'General','price':10,'provider_user_id':str(provider_id),'provider_name':'Provider'})
        assert response.status_code==201,response.text
        pid=response.json()['id']; url=f'/api/v1/products/{pid}'
        assert response.json()['provider_user_id']==str(provider_id)
        detail=client.get(url)
        assert detail.status_code==200,detail.text
        assert detail.json()['provider_name']=='Provider'
        assert client.put(url,json={'product_name':'Renamed'}).json()['provider_user_id']==str(provider_id)
        replacement=uuid4()
        assert client.put(url,json={'provider_user_id':str(replacement),'provider_name':'Replacement'}).json()['provider_user_id']==str(replacement)
        assert client.get(url).json()['provider_name']=='Replacement'
        cleared=client.put(url,json={'provider_user_id':None,'provider_name':None})
        assert cleared.status_code==200 and cleared.json()['provider_user_id'] is None
        assert client.get(url).json()['provider_name'] is None
        assert client.put(url,json={'provider_user_id':'invalid'}).status_code==422
        schema=app.openapi()['components']['schemas']
        for name in ('ProductCreate','ProductUpdate','ProductResponse','ProductDetailResponse'):
            assert {'provider_user_id','provider_name'} <= schema[name]['properties'].keys()
    engine.dispose()
