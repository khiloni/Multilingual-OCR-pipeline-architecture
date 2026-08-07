# Purpose: Lazy database engine creation, session factory, and a non-blocking connectivity health check fallback.
# Future TODOs: Configure pool sizing, connection recycling, and read replicas routing.

import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from app.core.config import settings

logger = logging.getLogger(__name__)

# Lazy initialization: Construct URL, but create engine without immediate connection tests.
DATABASE_URL = f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"

# Create engine with connect args and pool configuration
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # checks connection health when pulling from pool
    pool_recycle=3600
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db() -> Generator[Session, None, None]:
    """
    Dependency generator yielding db sessions. Sessions are opened and closed per request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def check_db_health() -> bool:
    """
    Checks database availability using a lightweight query. 
    Does not crash the system on connection failure, logging errors and returning False.
    """
    try:
        # Run a quick ping query
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        # Active-but-lazy fallback: Log and return failure status rather than raising a startup crash
        return False
