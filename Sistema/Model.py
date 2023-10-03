from __future__ import annotations
from typing import List

from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import relationship
class Base(DeclarativeBase):
    pass

from sqlalchemy import Column
from sqlalchemy import Table



from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import create_engine, MetaData, ForeignKey, Column, Integer, String, Float, DateTime, Boolean, engine
from sqlalchemy.orm import sessionmaker, Relationship


DBName = "Acesso.db"
fileEngine = create_engine('sqlite:///' + DBName, connect_args={'check_same_thread': False}, echo = False)

DBMemory = ":memory:"
memEngine = create_engine('sqlite:///' + DBMemory, connect_args={'check_same_thread': False}, echo = False)
memConnection = memEngine.raw_connection().connection
engine = fileEngine

meta = MetaData()
meta.bind = engine
Base = declarative_base(metadata=meta)
Session = sessionmaker(bind=engine)
db = Session()


usuarios_ambientes = Table(
    "usuarios_ambientes",
    Base.metadata,
    Column("usuario_id", ForeignKey("usuarios.id"), primary_key=True),
    Column("ambiente_id", ForeignKey("ambientes.id"), primary_key=True),
)

usuarios_ambientes_admins = Table(
    "usuarios_ambientesadmin",
    Base.metadata,
    Column("usuarioADM_id", ForeignKey("usuarios.id"), primary_key=True),
    Column("ambienteADM_id", ForeignKey("ambientes.id"), primary_key=True),
)

class Usuario (Base):
    __tablename__ = 'usuarios'
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(50))
    matricula: Mapped[str] = mapped_column(String(50))
    pin: Mapped[str] = mapped_column(String(4))
    admin: Mapped[bool] = mapped_column(Boolean)
    tag: Mapped["TAG"] = relationship(back_populates="usuario")
    mac: Mapped["MAC"] = relationship(back_populates="usuario")
    ambientes: Mapped[List[Ambiente]] = relationship(secondary=usuarios_ambientes, back_populates="frequentadores")
    ambientesAdmin: Mapped[List[Ambiente]] = relationship(secondary=usuarios_ambientes_admins, back_populates="admins")


class TAG (Base):
    __tablename__ = 'tags'
    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[str] = mapped_column(String(50))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    usuario: Mapped["Usuario"] = relationship(back_populates="tag")

class MAC (Base):
    id: Mapped[int] = mapped_column(primary_key=True)
    __tablename__ = 'macs'
    endereco = Column(String(50), nullable=False)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    usuario: Mapped["Usuario"] = relationship(back_populates="mac")

class Ambiente(Base):
    __tablename__ = 'ambientes'
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(50))
    local: Mapped[str] = mapped_column(String(50))
    frequentadores: Mapped[List[Usuario]] = relationship(secondary=usuarios_ambientes, back_populates="ambientes")
    admins: Mapped[List[Usuario]] = relationship(secondary=usuarios_ambientes_admins, back_populates="ambientesAdmin")
    cerberoses: Mapped[List[Cerberos]] = relationship(back_populates="ambiente")
    carontes: Mapped[List[Caronte]] = relationship(back_populates="ambiente")

class Cerberos(Base):
    __tablename__ = 'cerberoses'
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(50))
    mac: Mapped[str] = mapped_column(String(50))
    chave: Mapped[str] = mapped_column(String(50))
    ambiente_id: Mapped[int] = mapped_column(ForeignKey("ambientes.id"))
    ambiente: Mapped["Ambiente"] = relationship(back_populates="cerberoses")

class Caronte(Base):
    __tablename__ = 'carontes'
    id: Mapped[int] = mapped_column(primary_key=True)
    mac: Mapped[str] = mapped_column(String(50))
    chave: Mapped[str] = mapped_column(String(50))
    ambiente_id: Mapped[int] = mapped_column(ForeignKey("ambientes.id"))
    ambiente: Mapped["Ambiente"] = relationship(back_populates="carontes")

    def receberTAG(self, tag:TAG) -> bool:
        for user in self.ambiente.frequentadores:
            try:
                if user.tag.numero == tag.numero and user.tag.numero is not None:
                    return True
            except AttributeError:
                return False
        return False
        

meta.create_all(engine)