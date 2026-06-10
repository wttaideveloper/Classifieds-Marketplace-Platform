from sqlalchemy.orm import Session
from uuid import UUID
from app.models.community_ecosystem_model import CommunityEcosystem

def create_ecosystem_repo(db: Session, data: dict):
        ecosystem = CommunityEcosystem(**data)

        db.add(ecosystem)
        db.commit()
        db.refresh(ecosystem)

        return ecosystem


def get_all_ecosystem_repo(db: Session):
        return db.query(CommunityEcosystem).all()


def get_by_id_ecosystem_repo(db: Session, ecosystem_id: UUID):
        return (
            db.query(CommunityEcosystem)
            .filter(
                CommunityEcosystem.ecosystem_id == ecosystem_id
            )
            .first()
        )

def update_ecosytem_repo(
        db: Session,
        ecosystem: CommunityEcosystem,
        data: dict
    ):
        for key, value in data.items():
            setattr(ecosystem, key, value)

        db.commit()
        db.refresh(ecosystem)

        return ecosystem

   
def delete_ecosystem_repo(db: Session, ecosystem: CommunityEcosystem):
        db.delete(ecosystem)
        db.commit()