from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def get_session() -> Session:
    """
    用法：
        with get_session() as session:
            session.add(some_object)
            session.commit()
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
