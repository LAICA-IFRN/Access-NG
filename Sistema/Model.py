from __future__ import annotations
from typing import List, Optional
import datetime

from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase, relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import (
    create_engine, MetaData, ForeignKey, Column, Integer, String,
    DateTime, Boolean, Float, Table, text
)
from sqlalchemy.orm import sessionmaker


DBName = "Acesso.db"
engine = create_engine('sqlite:///' + DBName, connect_args={'check_same_thread': False}, echo=False)

meta = MetaData()
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


class Usuario(Base):
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


class TAG(Base):
    __tablename__ = 'tags'
    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[str] = mapped_column(String(50))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    usuario: Mapped["Usuario"] = relationship(back_populates="tag")


class MAC(Base):
    __tablename__ = 'macs'
    id: Mapped[int] = mapped_column(primary_key=True)
    endereco = Column(String(50), nullable=False)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    usuario: Mapped["Usuario"] = relationship(back_populates="mac")


class Ambiente(Base):
    __tablename__ = 'ambientes'
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(50))
    local: Mapped[str] = mapped_column(String(50))
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raio_metros: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    last_seen: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    coldstart_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)


class Caronte(Base):
    __tablename__ = 'carontes'
    id: Mapped[int] = mapped_column(primary_key=True)
    mac: Mapped[str] = mapped_column(String(50))
    chave: Mapped[str] = mapped_column(String(50))
    ambiente_id: Mapped[int] = mapped_column(ForeignKey("ambientes.id"))
    ambiente: Mapped["Ambiente"] = relationship(back_populates="carontes")
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    last_seen: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    coldstart_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    def receberTAG(self, tag: TAG) -> bool:
        for user in self.ambiente.frequentadores:
            try:
                if user.tag.numero == tag.numero and user.tag.numero is not None:
                    return True
            except AttributeError:
                return False
        return False


class AccessLog(Base):
    __tablename__ = 'access_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    mac: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)


meta.create_all(engine)


def _add_column_if_missing(table: str, column: str, col_type: str):
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        existing = [r[1] for r in rows]
        if column not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            conn.commit()


for _table in ('cerberoses', 'carontes'):
    _add_column_if_missing(_table, 'status', 'VARCHAR(20)')
    _add_column_if_missing(_table, 'last_seen', 'DATETIME')
    _add_column_if_missing(_table, 'coldstart_at', 'DATETIME')

_add_column_if_missing('ambientes', 'latitude', 'FLOAT')
_add_column_if_missing('ambientes', 'longitude', 'FLOAT')
_add_column_if_missing('ambientes', 'raio_metros', 'INTEGER')
