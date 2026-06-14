from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)


class Manufacturer(Base):
    __tablename__ = "manufacturers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    mfg_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    spools: Mapped[list["Spool"]] = relationship(back_populates="manufacturer")


class MaterialType(Base):
    __tablename__ = "material_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    spools: Mapped[list["Spool"]] = relationship(back_populates="material_type")


class Color(Base):
    __tablename__ = "colors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    color_code: Mapped[str | None] = mapped_column(String(7), nullable=True)  # HTML hex, e.g. #1A2B3C

    spools: Mapped[list["Spool"]] = relationship(back_populates="color")


class Spool(Base):
    __tablename__ = "spools"
    __table_args__ = (
        UniqueConstraint(
            "manufacturer_id", "material_type_id", "color_id", "weight", "sku",
            name="uq_spool_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[int] = mapped_column(ForeignKey("manufacturers.id"), nullable=False)
    material_type_id: Mapped[int] = mapped_column(ForeignKey("material_types.id"), nullable=False)
    color_id: Mapped[int] = mapped_column(ForeignKey("colors.id"), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)  # grams

    manufacturer: Mapped[Manufacturer] = relationship(back_populates="spools")
    material_type: Mapped[MaterialType] = relationship(back_populates="spools")
    color: Mapped[Color] = relationship(back_populates="spools")
    inventory: Mapped["SpoolInventory"] = relationship(
        back_populates="spool", cascade="all, delete-orphan", uselist=False
    )

    @property
    def qty(self) -> int:
        return self.inventory.qty if self.inventory else 0


class SpoolInventory(Base):
    __tablename__ = "spool_inventory"

    spool_id: Mapped[int] = mapped_column(ForeignKey("spools.id"), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    spool: Mapped[Spool] = relationship(back_populates="inventory")
