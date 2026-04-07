from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

DATABASE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "indexer.db")
os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DATABASE_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class FileEntry(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    extension = Column(String, nullable=False)
    size = Column(BigInteger, nullable=False)
    md5 = Column(String(32), nullable=True, index=True)
    content_hash = Column(String(64), nullable=True, index=True)
    created_time = Column(DateTime, nullable=True)
    modified_time = Column(DateTime, nullable=True)
    scan_time = Column(DateTime, default=datetime.now)
    is_duplicate = Column(Boolean, default=False)
    duplicate_of_id = Column(Integer, nullable=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
