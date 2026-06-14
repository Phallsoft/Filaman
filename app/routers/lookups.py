import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import flash, verify_csrf
from ..database import get_db
from ..models import Color, Manufacturer, MaterialType, Spool
from ..templating import templates

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

LOOKUPS = {
    "manufacturers": {
        "model": Manufacturer,
        "title": "Manufacturers",
        "singular": "Manufacturer",
        "fk": "manufacturer_id",
    },
    "materials": {
        "model": MaterialType,
        "title": "Material Types",
        "singular": "Material Type",
        "fk": "material_type_id",
    },
    "colors": {
        "model": Color,
        "title": "Colors",
        "singular": "Color",
        "fk": "color_id",
    },
}


def find_or_create(db: Session, model, name: str, **extra):
    """Case-insensitive find by name, creating the record if missing."""
    name = (name or "").strip()
    if not name:
        return None, False
    obj = db.query(model).filter(func.lower(model.name) == name.lower()).first()
    if obj:
        return obj, False
    obj = model(name=name, **extra)
    db.add(obj)
    db.flush()
    return obj, True


def _clean_extra(entity: str, mfg_url: str | None, color_code: str | None) -> dict:
    extra = {}
    if entity == "manufacturers":
        extra["mfg_url"] = (mfg_url or "").strip() or None
    elif entity == "colors":
        code = (color_code or "").strip()
        extra["color_code"] = code if HEX_RE.match(code) else None
    return extra


def _make_router(entity: str, cfg: dict) -> APIRouter:
    r = APIRouter(prefix=f"/{entity}")
    model = cfg["model"]

    @r.get("", name=f"{entity}_list")
    def list_items(request: Request, db: Session = Depends(get_db)):
        items = db.query(model).order_by(func.lower(model.name)).all()
        return templates.TemplateResponse(
            request, "lookup_list.html", {"entity": entity, "cfg": cfg, "items": items}
        )

    @r.post("", name=f"{entity}_create")
    def create_item(
        request: Request,
        name: str = Form(...),
        mfg_url: str = Form(None),
        color_code: str = Form(None),
        csrf_token: str = Form(None),
        db: Session = Depends(get_db),
    ):
        verify_csrf(request, csrf_token)
        obj, created = find_or_create(db, model, name, **_clean_extra(entity, mfg_url, color_code))
        if obj is None:
            flash(request, "Name is required.", "error")
        elif created:
            db.commit()
            flash(request, f"{cfg['singular']} '{obj.name}' added.", "success")
        else:
            flash(request, f"{cfg['singular']} '{obj.name}' already exists.", "error")
        return RedirectResponse(f"/{entity}", status_code=303)

    @r.post("/quick", name=f"{entity}_quick")
    def quick_create(
        request: Request,
        name: str = Form(None, alias=f"qname_{entity}"),
        color_code: str = Form(None, alias=f"qcolor_{entity}"),
        csrf_token: str = Form(None),
        db: Session = Depends(get_db),
    ):
        verify_csrf(request, csrf_token)
        obj, created = find_or_create(db, model, name, **_clean_extra(entity, None, color_code))
        if created:
            db.commit()
        items = db.query(model).order_by(func.lower(model.name)).all()
        return templates.TemplateResponse(
            request,
            "partials/_options.html",
            {"items": items, "selected": obj.id if obj else None},
        )

    @r.post("/{item_id}/delete", name=f"{entity}_delete")
    def delete_item(
        request: Request,
        item_id: int,
        csrf_token: str = Form(None),
        db: Session = Depends(get_db),
    ):
        verify_csrf(request, csrf_token)
        obj = db.get(model, item_id)
        if obj:
            in_use = db.query(Spool.id).filter(getattr(Spool, cfg["fk"]) == item_id).first()
            if in_use:
                flash(request, f"Cannot delete '{obj.name}' — it is used by one or more spools.", "error")
            else:
                db.delete(obj)
                db.commit()
                flash(request, f"{cfg['singular']} '{obj.name}' deleted.", "success")
        return RedirectResponse(f"/{entity}", status_code=303)

    @r.post("/{item_id}", name=f"{entity}_update")
    def update_item(
        request: Request,
        item_id: int,
        name: str = Form(...),
        mfg_url: str = Form(None),
        color_code: str = Form(None),
        csrf_token: str = Form(None),
        db: Session = Depends(get_db),
    ):
        verify_csrf(request, csrf_token)
        obj = db.get(model, item_id)
        if obj:
            new_name = name.strip()
            if not new_name:
                flash(request, "Name is required.", "error")
                return RedirectResponse(f"/{entity}", status_code=303)
            clash = (
                db.query(model)
                .filter(func.lower(model.name) == new_name.lower(), model.id != item_id)
                .first()
            )
            if clash:
                flash(request, f"'{new_name}' already exists.", "error")
                return RedirectResponse(f"/{entity}", status_code=303)
            obj.name = new_name
            for field, value in _clean_extra(entity, mfg_url, color_code).items():
                setattr(obj, field, value)
            db.commit()
            flash(request, f"{cfg['singular']} updated.", "success")
        return RedirectResponse(f"/{entity}", status_code=303)

    return r


router = APIRouter()
for _entity, _cfg in LOOKUPS.items():
    router.include_router(_make_router(_entity, _cfg))
