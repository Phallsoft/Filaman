from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select as sa_select
from sqlalchemy.orm import Session, joinedload

from ..auth import current_user, flash, verify_csrf
from ..database import get_db, get_owned
from ..models import Color, Manufacturer, MaterialType, Spool, SpoolInventory, User
from ..services.images import ImageError, delete_image, fetch_remote_image, save_spool_image
from ..templating import templates

router = APIRouter()


def _lookup_lists(db: Session, user_id: int) -> dict:
    return {
        "manufacturers": db.query(Manufacturer).filter(Manufacturer.user_id == user_id).order_by(func.lower(Manufacturer.name)).all(),
        "materials": db.query(MaterialType).filter(MaterialType.user_id == user_id).order_by(func.lower(MaterialType.name)).all(),
        "colors": db.query(Color).filter(Color.user_id == user_id).order_by(func.lower(Color.name)).all(),
    }


def _lookups_belong_to(db: Session, user_id: int, manufacturer_id: int, material_type_id: int, color_id: int) -> bool:
    return (
        get_owned(db, Manufacturer, manufacturer_id, user_id) is not None
        and get_owned(db, MaterialType, material_type_id, user_id) is not None
        and get_owned(db, Color, color_id, user_id) is not None
    )


def merge_or_create_spool(
    db: Session,
    user_id: int,
    manufacturer_id: int,
    material_type_id: int,
    color_id: int,
    sku: str | None,
    weight: int,
    qty: int,
    image_path: str | None = None,
) -> tuple[Spool, bool]:
    """Create a spool, or increment qty on an identical existing one. Returns (spool, merged)."""
    sku = (sku or "").strip() or None
    existing = (
        db.query(Spool)
        .filter(
            Spool.user_id == user_id,
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
        if image_path and not existing.image_path:
            existing.image_path = image_path
        return existing, True
    spool = Spool(
        user_id=user_id,
        manufacturer_id=manufacturer_id,
        material_type_id=material_type_id,
        color_id=color_id,
        sku=sku,
        weight=weight,
        image_path=image_path,
    )
    spool.inventory = SpoolInventory(qty=qty)
    db.add(spool)
    return spool, False


@router.get("/")
def home():
    return RedirectResponse("/spools", status_code=303)


@router.get("/spools")
def list_spools(
    request: Request,
    q: str = "",
    sort: str = "manufacturer",
    mfr: str = "",
    mat: str = "",
    col: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    show_images = request.cookies.get("filaman_show_images", "1") != "0"
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
        .filter(Spool.user_id == user.id)
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
    mfr_id = int(mfr) if mfr else None
    mat_id = int(mat) if mat else None
    col_id = int(col) if col else None
    if mfr_id:
        query = query.filter(Spool.manufacturer_id == mfr_id)
    if mat_id:
        query = query.filter(Spool.material_type_id == mat_id)
    if col_id:
        query = query.filter(Spool.color_id == col_id)

    # Correlated subquery for qty — avoids duplicate-row issues with joinedload
    _qty_subq = (
        sa_select(SpoolInventory.qty)
        .where(SpoolInventory.spool_id == Spool.id)
        .correlate(Spool)
        .scalar_subquery()
    )
    _SORT_MAP = {
        "color":        func.lower(Color.name),
        "material":     func.lower(MaterialType.name),
        "manufacturer": func.lower(Manufacturer.name),
        "weight":       Spool.weight,
        "sku":          func.lower(Spool.sku),
        "qty":          _qty_subq,
    }
    desc_sort = sort.startswith("-")
    sort_key = sort.lstrip("-")
    sort_expr = _SORT_MAP.get(sort_key, _SORT_MAP["manufacturer"])
    if desc_sort:
        sort_expr = sort_expr.desc()

    spools = query.order_by(sort_expr).all()
    total_qty = sum(s.qty for s in spools)
    return templates.TemplateResponse(
        request, "spool_list.html", {
            "spools": spools, "q": q, "total_qty": total_qty,
            "sort": sort, "mfr": mfr, "mat": mat, "col": col,
            "show_images": show_images,
            **_lookup_lists(db, user.id),
        }
    )


@router.get("/spools/new")
def new_spool(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return templates.TemplateResponse(
        request, "spool_form.html", {"spool": None, **_lookup_lists(db, user.id)}
    )


@router.post("/spools")
async def create_spool(
    request: Request,
    manufacturer_id: int = Form(...),
    material_type_id: int = Form(...),
    color_id: int = Form(...),
    sku: str = Form(None),
    weight: int = Form(...),
    qty: int = Form(1),
    image_file: UploadFile | None = File(None),
    image_url: str = Form(None),
    cropped_image: str = Form(None),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    if weight <= 0 or qty < 0:
        flash(request, "Weight must be positive and quantity cannot be negative.", "error")
        return RedirectResponse("/spools/new", status_code=303)
    if not _lookups_belong_to(db, user.id, manufacturer_id, material_type_id, color_id):
        flash(request, "Please choose a manufacturer, material, and color from your lists.", "error")
        return RedirectResponse("/spools/new", status_code=303)
    try:
        image_path = await save_spool_image(
            upload=image_file,
            source_url=(image_url or "").strip() or None,
            cropped_image=(cropped_image or "").strip() or None,
        )
    except ImageError as e:
        flash(request, str(e), "error")
        return RedirectResponse("/spools/new", status_code=303)
    spool, merged = merge_or_create_spool(
        db, user.id, manufacturer_id, material_type_id, color_id, sku, weight, qty, image_path
    )
    if image_path and merged and spool.image_path != image_path:
        delete_image(image_path)
    db.commit()
    if merged:
        flash(request, f"Identical spool already existed — quantity increased by {qty}.", "success")
    else:
        flash(request, f"Spool added with quantity {qty}.", "success")
    return RedirectResponse("/spools", status_code=303)


@router.get("/spools/image-proxy")
def image_proxy(url: str, user: User = Depends(current_user)):
    try:
        content, content_type = fetch_remote_image(url)
    except ImageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(content, media_type=content_type)


@router.get("/spools/{spool_id}/edit")
def edit_spool(
    request: Request,
    spool_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    spool = get_owned(db, Spool, spool_id, user.id)
    if not spool:
        flash(request, "Spool not found.", "error")
        return RedirectResponse("/spools", status_code=303)
    return templates.TemplateResponse(
        request, "spool_form.html", {"spool": spool, **_lookup_lists(db, user.id)}
    )


@router.post("/spools/{spool_id}")
async def update_spool(
    request: Request,
    spool_id: int,
    manufacturer_id: int = Form(...),
    material_type_id: int = Form(...),
    color_id: int = Form(...),
    sku: str = Form(None),
    weight: int = Form(...),
    qty: int = Form(0),
    image_file: UploadFile | None = File(None),
    image_url: str = Form(None),
    cropped_image: str = Form(None),
    clear_image: str = Form(None),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    spool = get_owned(db, Spool, spool_id, user.id)
    if not spool:
        flash(request, "Spool not found.", "error")
        return RedirectResponse("/spools", status_code=303)
    if weight <= 0 or qty < 0:
        flash(request, "Weight must be positive and quantity cannot be negative.", "error")
        return RedirectResponse(f"/spools/{spool_id}/edit", status_code=303)
    if not _lookups_belong_to(db, user.id, manufacturer_id, material_type_id, color_id):
        flash(request, "Please choose a manufacturer, material, and color from your lists.", "error")
        return RedirectResponse(f"/spools/{spool_id}/edit", status_code=303)
    if clear_image == "1":
        delete_image(spool.image_path)
        spool.image_path = None
    else:
        try:
            image_path = await save_spool_image(
                upload=image_file,
                source_url=(image_url or "").strip() or None,
                cropped_image=(cropped_image or "").strip() or None,
                replace_path=spool.image_path,
            )
        except ImageError as e:
            flash(request, str(e), "error")
            return RedirectResponse(f"/spools/{spool_id}/edit", status_code=303)
        if image_path:
            spool.image_path = image_path
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
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    spool = get_owned(db, Spool, spool_id, user.id)
    if spool:
        delete_image(spool.image_path)
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
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    spool = get_owned(db, Spool, spool_id, user.id)
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
