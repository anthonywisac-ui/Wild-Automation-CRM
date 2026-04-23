from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import json

DATABASE_URL = "sqlite:///./cms.db"  # or use PostgreSQL
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class BotConfig(Base):
    __tablename__ = "bot_configs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    bot_type = Column(String)  # "order" or "appointment"
    config_json = Column(Text)  # full JSON config
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    hashed_password = Column(String)

Base.metadata.create_all(bind=engine)