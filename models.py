from sqlalchemy import create_engine, Column, Integer, String, DateTime, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class DomainLookup(Base):
    __tablename__ = 'domain_lookups'  

    id = Column(Integer, primary_key=True, index=True)  
    domain_name = Column(String, index=True)  
    status = Column(String)  
    registrar = Column(String)  
    name_servers = Column(String)  
    created_on = Column(String)  
    last_updated_on = Column(String)  
    expiration_date = Column(String)  
    timestamp = Column(DateTime, default=func.now()) 


Base.metadata.create_all(bind=engine)
