from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint, func
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LibraryCategory(Base):
    __tablename__ = "library_categories"
    # SQLite treats NULL parent_ids as distinct, so top-level sibling uniqueness
    # is enforced in services.library_taxonomy as well.
    __table_args__ = (UniqueConstraint("user_id", "parent_id", "name", name="uq_library_category_sibling"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("library_categories.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class LibraryTag(Base):
    __tablename__ = "library_tags"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_library_tag_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)


library_model_tags = Table(
    "library_model_tags",
    Base.metadata,
    Column("model_id", ForeignKey("library_models.id"), primary_key=True),
    Column("tag_id", ForeignKey("library_tags.id"), primary_key=True),
)


class StoredFile(Base):
    """One blob in FILES_DIR, keyed by content hash and shared by every row that references it."""

    __tablename__ = "stored_files"
    # AUTOINCREMENT: ids are never reused, so a stale reference or a cached /models/images/<id>
    # can never alias newer content.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)


class LibraryModel(Base):
    __tablename__ = "library_models"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    print_settings: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("library_categories.id"), nullable=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    copied_from_id: Mapped[int | None] = mapped_column(ForeignKey("library_models.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    owner: Mapped[User] = relationship()
    category: Mapped[LibraryCategory | None] = relationship()
    copied_from: Mapped["LibraryModel | None"] = relationship(remote_side=[id])
    tags: Mapped[list[LibraryTag]] = relationship(
        secondary=library_model_tags, order_by=lambda: func.lower(LibraryTag.name)
    )
    files: Mapped[list["LibraryModelFile"]] = relationship(
        back_populates="model", cascade="all, delete-orphan",
        order_by=lambda: (LibraryModelFile.position, LibraryModelFile.id),
    )
    images: Mapped[list["LibraryModelImage"]] = relationship(
        back_populates="model", cascade="all, delete-orphan",
        order_by=lambda: (LibraryModelImage.position, LibraryModelImage.id),
    )

    @property
    def cover(self) -> "LibraryModelImage | None":
        return self.images[0] if self.images else None


class LibraryModelFile(Base):
    __tablename__ = "library_model_files"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("library_models.id"), nullable=False)
    stored_file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    model: Mapped[LibraryModel] = relationship(back_populates="files")
    stored: Mapped[StoredFile] = relationship()


class LibraryModelImage(Base):
    __tablename__ = "library_model_images"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("library_models.id"), nullable=False)
    stored_file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    model: Mapped[LibraryModel] = relationship(back_populates="images")
    stored: Mapped[StoredFile] = relationship()
