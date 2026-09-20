from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")


class Manufacturer(Base):
    __tablename__ = "manufacturers"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_manufacturer_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    mfg_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    spools: Mapped[list["Spool"]] = relationship(back_populates="manufacturer")


class MaterialType(Base):
    __tablename__ = "material_types"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_material_type_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    spools: Mapped[list["Spool"]] = relationship(back_populates="material_type")


class Color(Base):
    __tablename__ = "colors"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_color_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color_code: Mapped[str | None] = mapped_column(String(7), nullable=True)  # HTML hex, e.g. #1A2B3C

    spools: Mapped[list["Spool"]] = relationship(back_populates="color")


class Spool(Base):
    __tablename__ = "spools"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "manufacturer_id", "material_type_id", "color_id", "weight", "sku",
            name="uq_spool_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    manufacturer_id: Mapped[int] = mapped_column(ForeignKey("manufacturers.id"), nullable=False)
    material_type_id: Mapped[int] = mapped_column(ForeignKey("material_types.id"), nullable=False)
    color_id: Mapped[int] = mapped_column(ForeignKey("colors.id"), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)  # grams
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    manufacturer: Mapped[Manufacturer] = relationship(back_populates="spools")
    material_type: Mapped[MaterialType] = relationship(back_populates="spools")
    color: Mapped[Color] = relationship(back_populates="spools")
    inventory: Mapped["SpoolInventory"] = relationship(
        back_populates="spool", cascade="all, delete-orphan", uselist=False
    )

    @property
    def qty(self) -> int:
        return self.inventory.qty if self.inventory else 0

    @property
    def image_url(self) -> str | None:
        return f"/media/{self.image_path}" if self.image_path else None


class SpoolInventory(Base):
    __tablename__ = "spool_inventory"

    spool_id: Mapped[int] = mapped_column(ForeignKey("spools.id"), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    spool: Mapped[Spool] = relationship(back_populates="inventory")
