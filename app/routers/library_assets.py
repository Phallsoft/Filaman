"""Model library files and images: access-checked serving plus owner-only management."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..auth import current_user, flash, verify_csrf
from ..database import get_db, get_owned
from ..models import LibraryModel, LibraryModelFile, LibraryModelImage, User
from ..services import file_store
from ..services.library import get_viewable, safe_filename

router = APIRouter(prefix="/models")


def _viewable_asset(db: Session, cls, asset_id: int, user: User):
    asset = db.get(cls, asset_id)
    if asset is None or get_viewable(db, asset.model_id, user) is None:
        raise HTTPException(status_code=404, detail="Not found")
    path = file_store.blob_path(asset.stored.sha256)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing")
    return asset, path


@router.get("/files/{file_id:int}")
def download_file(file_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    asset, path = _viewable_asset(db, LibraryModelFile, file_id, user)
    return FileResponse(path, media_type=asset.stored.content_type, filename=safe_filename(asset.filename))


@router.get("/images/{image_id:int}")
def show_image(image_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    asset, path = _viewable_asset(db, LibraryModelImage, image_id, user)
    # Library images are always normalized to JPEG; never trust the stored type of a shared blob.
    return FileResponse(path, media_type="image/jpeg", headers={
        "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
    })


def _owned_asset(db: Session, cls, model_id: int, asset_id: int, user: User):
    model = get_owned(db, LibraryModel, model_id, user.id)
    asset = db.get(cls, asset_id)
    if model is None or asset is None or asset.model_id != model.id:
        raise HTTPException(status_code=404, detail="Not found")
    return model, asset


def _back(model_id: int) -> RedirectResponse:
    return RedirectResponse(f"/models/{model_id}/edit", status_code=303)


@router.post("/{model_id:int}/files/{file_id:int}/rename")
def rename_file(
    request: Request,
    model_id: int,
    file_id: int,
    filename: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    _, asset = _owned_asset(db, LibraryModelFile, model_id, file_id, user)
    if filename.strip():
        asset.filename = safe_filename(filename)
        db.commit()
        flash(request, "File renamed.", "success")
    return _back(model_id)


@router.post("/{model_id:int}/files/{file_id:int}/delete")
def delete_file(
    request: Request,
    model_id: int,
    file_id: int,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    model, asset = _owned_asset(db, LibraryModelFile, model_id, file_id, user)
    stored_id = asset.stored_file_id
    model.files.remove(asset)
    file_store.release(db, [stored_id])
    db.commit()
    flash(request, "File removed.", "success")
    return _back(model_id)


@router.post("/{model_id:int}/images/{image_id:int}/delete")
def delete_image(
    request: Request,
    model_id: int,
    image_id: int,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    model, asset = _owned_asset(db, LibraryModelImage, model_id, image_id, user)
    stored_id = asset.stored_file_id
    model.images.remove(asset)
    file_store.release(db, [stored_id])
    db.commit()
    flash(request, "Image removed.", "success")
    return _back(model_id)


@router.post("/{model_id:int}/images/{image_id:int}/cover")
def make_cover(
    request: Request,
    model_id: int,
    image_id: int,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    model, asset = _owned_asset(db, LibraryModelImage, model_id, image_id, user)
    ordered = [asset] + [img for img in model.images if img.id != asset.id]
    for position, img in enumerate(ordered):
        img.position = position
    db.commit()
    return _back(model_id)
