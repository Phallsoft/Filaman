from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..auth import flash, verify_csrf
from ..database import get_db
from ..models import Color, Manufacturer, MaterialType, Spool, SpoolInventory
from ..templating import templates

router = APIRouter()


def _lookup_lists(db: Session) -> dict:
    return {
        "manufacturers": db.query(Manufacturer).order_by(func.lower(Manufacturer.name)).all(),
        "materials": db.query(MaterialType).order_by(func.lower(MaterialType.name)).all(),
        "colors": db.query(Color).order_by(func.lower(Color.name)).all(),
    }


def merge_or_create_spool(
    db: Session,
    manufacturer_id: int,
    material_type_id: int,
    color_id: int,
    sku: str | None,
    weight: int,
    qty: int,
) -> tuple[Spool, bool]:
    """Create a spool, or increment qty on an identical existing one. Returns (spool, merged)."""
    sku = (sku or "").strip() or None
    existing = (
        db.query(Spool)
        .filter(
            Spool.manufacturer_id == manufacturer_id,
            Spool.material_type_id == material_type_id,
            Spool.color_id == color_id,
            Spool.weight == weight,
            Spool.sku == sku,
        )
        .first()
    )
    if existing:
        if existing.inventory:
            existing.inventory.qty += qty
        else:
            existing.inventory = SpoolInventory(qty=qty)
        return existing, True
    spool = Spool(
        manufacturer_id=manufacturer_id,
        material_type_id=material_type_id,
        color_id=color_id,
        sku=sku,
        weight=weight,
    )
    spool.inventory = SpoolInventory(qty=qty)
    db.add(spool)
    return spool, False


@router.get("/")
def home():
    return RedirectResponse("/spools", status_code=303)


@router.get("/spools")
def list_spools(request: Request, q: str = "", db: Session = Depends(get_db)):
    query = (
        db.query(Spool)
        .options(
            joinedload(Spool.manufacturer),
            joinedload(Spool.material_type),
            joinedload(Spool.color),
            joinedload(Spool.inventory),
        )
        .join(Manufacturer)
        .join(MaterialType)
        .join(Color)
    )
    q = q.strip()
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Manufacturer.name.ilike(like),
                MaterialType.name.ilike(like),
                Color.name.ilike(like),
                Spool.sku.ilike(like),
            )
        )
    spools = query.order_by(
        func.lower(Manufacturer.name), func.lower(MaterialType.name), func.lower(Color.name)
    ).all()
    total_qty = sum(s.qty for s in spools)
    return templates.TemplateResponse(
        request, "spool_list.html", {"spools": spools, "q": q, "total_qty": total_qty}
    )


@router.get("/spools/new")
def new_spool(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "spool_form.html", {"spool": None, **_lookup_lists(db)}
    )


@router.post("/spools")
def create_spool(
    request: Request,
    manufacturer_id: int = Form(...),
    material_type_id: int = Form(...),
    color_id: int = Form(...),
    sku: str = Form(None),
    weight: int = Form(...),
    qty: int = Form(1),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    if weight <= 0 or qty < 0:
        flash(request, "Weight must be positive and quantity cannot be negative.", "error")
        return RedirectResponse("/spools/new", status_code=303)
    spool, merged = merge_or_create_spool(
        db, manufacturer_id, material_type_id, color_id, sku, weight, qty
    )
    db.commit()
    if merged:
        flash(request, f"Identical spool already existed — quantity increased by {qty}.", "success")
    else:
        flash(request, f"Spool added with quantity {qty}.", "success")
    return RedirectResponse("/spools", status_code=303)


@router.get("/spools/{spool_id}/edit")
def edit_spool(request: Request, spool_id: int, db: Session = Depends(get_db)):
    spool = db.get(Spool, spool_id)
    if not spool:
        flash(request, "Spool not found.", "error")
        return RedirectResponse("/spools", status_code=303)
    return templates.TemplateResponse(
        request, "spool_form.html", {"spool": spool, **_lookup_lists(db)}
    )


@router.post("/spools/{spool_id}")
def update_spool(
    request: Request,
    spool_id: int,
    manufacturer_id: int = Form(...),
    material_type_id: int = Form(...),
    color_id: int = Form(...),
    sku: str = Form(None),
    weight: int = Form(...),
    qty: int = Form(0),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    spool = db.get(Spool, spool_id)
    if not spool:
        flash(request, "Spool not found.", "error")
        return RedirectResponse("/spools", status_code=303)
    if weight <= 0 or qty < 0:
        flash(request, "Weight must be positive and quantity cannot be negative.", "error")
        return RedirectResponse(f"/spools/{spool_id}/edit", status_code=303)
    spool.manufacturer_id = manufacturer_id
    spool.material_type_id = material_type_id
    spool.color_id = color_id
    spool.sku = (sku or "").strip() or None
    spool.weight = weight
    if spool.inventory:
        spool.inventory.qty = qty
    else:
        spool.inventory = SpoolInventory(qty=qty)
    db.commit()
    flash(request, "Spool updated.", "success")
    return RedirectResponse("/spools", status_code=303)


@router.post("/spools/{spool_id}/delete")
def delete_spool(
    request: Request,
    spool_id: int,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    spool = db.get(Spool, spool_id)
    if spool:
        db.delete(spool)
        db.commit()
        flash(request, "Spool deleted.", "success")
    return RedirectResponse("/spools", status_code=303)


@router.post("/spools/{spool_id}/adjust")
def adjust_qty(
    request: Request,
    spool_id: int,
    delta: int = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    spool = db.get(Spool, spool_id)
    if not spool:
        return templates.TemplateResponse(
            request, "partials/_qty_cell.html", {"spool": None}
        )
    if spool.inventory:
        spool.inventory.qty = max(0, spool.inventory.qty + delta)
    else:
        spool.inventory = SpoolInventory(qty=max(0, delta))
    db.commit()
    return templates.TemplateResponse(request, "partials/_qty_cell.html", {"spool": spool})
