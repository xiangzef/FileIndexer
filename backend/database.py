from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, BigInteger, Text, Float, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
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
    status = Column(String(20), default='available')
    source_path = Column(String, nullable=True)
    scan_record_id = Column(Integer, nullable=True)
    embedding_vector = Column(Text, nullable=True)
    content_summary = Column(Text, nullable=True)
    tag_status = Column(String(20), default='pending')

    tags = relationship("FileTag", back_populates="file", cascade="all, delete-orphan")

class ScanRecord(Base):
    __tablename__ = "scan_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_path = Column(String, nullable=False)
    scan_time = Column(DateTime, default=datetime.now)
    total_files = Column(Integer, default=0)
    total_size = Column(BigInteger, default=0)
    status = Column(String(20), default='active')
    stats_json = Column(String, nullable=True)

class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    category = Column(String(50), nullable=True)
    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)

    file_tags = relationship("FileTag", back_populates="tag", cascade="all, delete-orphan")

class FileTag(Base):
    __tablename__ = "file_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("files.id"), nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False)
    confidence = Column(Float, default=1.0)
    source = Column(String(20), default='ai')
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (UniqueConstraint('file_id', 'tag_id', name='unique_file_tag'),)

    file = relationship("FileEntry", back_populates="tags")
    tag = relationship("Tag", back_populates="file_tags")

Base.metadata.create_all(bind=engine)

def migrate_database():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    if 'scan_records' not in existing_tables:
        with engine.connect() as conn:
            conn.execute(text('''
                CREATE TABLE scan_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_path VARCHAR NOT NULL,
                    scan_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_files INTEGER DEFAULT 0,
                    total_size BIGINT DEFAULT 0,
                    status VARCHAR(20) DEFAULT 'active',
                    stats_json VARCHAR
                )
            '''))
            conn.commit()

    existing_columns = [col['name'] for col in inspector.get_columns('files')]

    if 'status' not in existing_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE files ADD COLUMN status VARCHAR(20) DEFAULT "available"'))
            conn.commit()

    if 'source_path' not in existing_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE files ADD COLUMN source_path VARCHAR'))
            conn.commit()

    if 'scan_record_id' not in existing_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE files ADD COLUMN scan_record_id INTEGER'))
            conn.commit()

    if 'embedding_vector' not in existing_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE files ADD COLUMN embedding_vector TEXT'))
            conn.commit()

    if 'content_summary' not in existing_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE files ADD COLUMN content_summary TEXT'))
            conn.commit()

    if 'tag_status' not in existing_columns:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE files ADD COLUMN tag_status VARCHAR(20) DEFAULT "pending"'))
            conn.commit()

    if 'tags' not in existing_tables:
        with engine.connect() as conn:
            conn.execute(text('''
                CREATE TABLE tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    category VARCHAR(50),
                    usage_count INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            '''))
            conn.commit()

    if 'file_tags' not in existing_tables:
        with engine.connect() as conn:
            conn.execute(text('''
                CREATE TABLE file_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    confidence FLOAT DEFAULT 1.0,
                    source VARCHAR(20) DEFAULT 'ai',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (file_id) REFERENCES files(id),
                    FOREIGN KEY (tag_id) REFERENCES tags(id),
                    UNIQUE(file_id, tag_id)
                )
            '''))
            conn.commit()

migrate_database()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()