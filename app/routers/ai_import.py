from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..auth import current_user, flash, verify_csrf
from ..database import get_db
from ..models import Color, Manufacturer, MaterialType, User
from ..services import ai_import as svc
from ..services.ai_import import ImportError_
from ..services.images import ImageError, delete_image, save_spool_image
from ..templating import templates
from .lookups import HEX_RE, find_or_create
from .spools import merge_or_create_spool

router = APIRouter(prefix="/import")


def _suggestions(db: Session, user_id: int) -> dict:
    return {
        "manufacturer_names": [m.name for m in db.query(Manufacturer).filter(Manufacturer.user_id == user_id).order_by(func.lower(Manufacturer.name))],
        "material_names": [m.name for m in db.query(MaterialType).filter(MaterialType.user_id == user_id).order_by(func.lower(MaterialType.name))],
        "color_names": [c.name for c in db.query(Color).filter(Color.user_id == user_id).order_by(func.lower(Color.name))],
    }


@router.get("")
def import_page(request: Request):
    return templates.TemplateResponse(request, "import_form.html")


@router.post("/parse")
def parse_import(
    request: Request,
    url: str = Form(None),
    text: str = Form(None),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    url = (url or "").strip()
    text = (text or "").strip()
    if not url and not text:
        flash(request, "Provide a product URL or paste some text.", "error")
        return templates.TemplateResponse(request, "import_form.html")

    source = text
    try:
        used_brightdata = False
        image_candidates = []
        if url and not text:
            source, used_brightdata, image_candidates = svc.fetch_page_data(url)
        fields = svc.extract_fields(source)
        if not svc.has_extracted_anything(fields):
            # Direct fetch may have hit a bot-protection page; retry through Bright Data.
            if url and not text and not used_brightdata and config.BRIGHTDATA_MCP_URL:
                source = svc.fetch_via_brightdata(url)
                fields = svc.extract_fields(source)
            if not svc.has_extracted_anything(fields):
                raise ImportError_("Couldn't find any filament details on that page (it may be blocking automated access).")
    except ImportError_ as e:
        msg = str(e)
        if url and not text:
            msg += " Try copying the relevant product text from the page and pasting it below instead."
        flash(request, msg, "error")
        return templates.TemplateResponse(
            request, "import_form.html", {"form_url": url, "form_text": text}
        )

    flash(request, "Review the extracted details below, edit as needed, then save.", "info")
    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {"fields": fields, "source_url": url, "image_candidates": image_candidates, **_suggestions(db, user.id)},
    )


@router.post("/save")
async def save_import(
    request: Request,
    manufacturer: str = Form(...),
    material: str = Form(...),
    color_name: str = Form(...),
    color_hex: str = Form(None),
    sku: str = Form(None),
    weight: int = Form(...),
    qty: int = Form(1),
    image_file: UploadFile | None = File(None),
    image_url: str = Form(None),
    cropped_image: str = Form(None),
    clear_image: str = Form(None),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    if weight <= 0 or qty < 1:
        flash(request, "Weight must be positive and quantity at least 1.", "error")
        return RedirectResponse("/import", status_code=303)
    image_path = None
    if clear_image != "1":
        try:
            image_path = await save_spool_image(
                upload=image_file,
                source_url=(image_url or "").strip() or None,
                cropped_image=(cropped_image or "").strip() or None,
            )
        except ImageError as e:
            flash(request, str(e), "error")
            return RedirectResponse("/import", status_code=303)

    mfg, mfg_new = find_or_create(db, Manufacturer, manufacturer, user.id)
    mat, mat_new = find_or_create(db, MaterialType, material, user.id)
    code = (color_hex or "").strip()
    col, col_new = find_or_create(
        db, Color, color_name, user.id, color_code=code if HEX_RE.match(code) else None
    )
    if not (mfg and mat and col):
        flash(request, "Manufacturer, material, and color are required.", "error")
        return RedirectResponse("/import", status_code=303)

    spool, merged = merge_or_create_spool(db, user.id, mfg.id, mat.id, col.id, sku, weight, qty, image_path)
    if image_path and merged and spool.image_path != image_path:
        delete_image(image_path)
    db.commit()

    created = [n for n, is_new in [(mfg.name, mfg_new), (mat.name, mat_new), (col.name, col_new)] if is_new]
    msg = f"Spool imported (qty {qty})."
    if created:
        msg += f" Created: {', '.join(created)}."
    if merged:
        msg += " Quantity was merged into an identical existing spool."
    flash(request, msg, "success")
    return RedirectResponse("/spools", status_code=303)
