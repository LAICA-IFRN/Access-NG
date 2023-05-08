from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import create_engine, MetaData, ForeignKey, Column, Integer, String, Float, DateTime, Boolean, engine
from sqlalchemy.orm import sessionmaker


DBName = "Acesso.db"
fileEngine = create_engine('sqlite:///' + DBName, connect_args={'check_same_thread': False}, echo = False)

DBMemory = ":memory:"
memEngine = create_engine('sqlite:///' + DBMemory, connect_args={'check_same_thread': False}, echo = False)
memConnection = memEngine.raw_connection().connection
engine = fileEngine
Session = sessionmaker(bind=engine)
db = Session()
meta = MetaData()
meta.bind = engine

Base = declarative_base(metadata=meta)
class Usuario (Base):
    __tablename__ = 'usuarios'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
meta.create_all()