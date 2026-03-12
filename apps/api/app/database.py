import uuid as uuid_module
from sqlalchemy import create_engine, types
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

class UUIDType(types.TypeDecorator):
    impl = types.String(36)
    cache_ok = True
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return uuid_module.UUID(str(value))

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
