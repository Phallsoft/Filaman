import datetime
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..auth import flash, hash_password, verify_csrf, verify_password
from ..database import get_db, reset_db
from ..models import User
from ..services import backup as backup_svc
from ..templating import templates

router = APIRouter(prefix="/admin")

RESET_PHRASE = "Yes, Really Reset The Database"


@router.get("")
def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html", {"reset_phrase": RESET_PHRASE})


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    user = db.get(User, request.session.get("user_id"))
    if not user or not verify_password(current_password, user.hashed_password):
        flash(request, "Current password is incorrect.", "error")
    elif len(new_password) < 8:
        flash(request, "New password must be at least 8 characters.", "error")
    elif new_password != confirm_password:
        flash(request, "New passwords do not match.", "error")
    else:
        user.hashed_password = hash_password(new_password)
        db.commit()
        flash(request, "Password changed.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.get("/backup")
def download_backup():
    tmp_path = backup_svc.create_backup()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return FileResponse(
        tmp_path,
        filename=f"filaman-backup-{stamp}.db",
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, tmp_path),
    )


@router.post("/restore")
async def restore_backup(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(None),
):
    verify_csrf(request, csrf_token)
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
        error = backup_svc.validate_backup(tmp_path)
        if error:
            flash(request, f"Restore failed: {error}", "error")
            return RedirectResponse("/admin", status_code=303)
        backup_svc.restore_backup(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.post("/reset")
def reset_database(
    request: Request,
    confirm_text: str = Form(""),
    csrf_token: str = Form(None),
):
    verify_csrf(request, csrf_token)
    if confirm_text != RESET_PHRASE:
        flash(request, f'Reset cancelled — you must type exactly "{RESET_PHRASE}".', "error")
        return RedirectResponse("/admin", status_code=303)
    reset_db()
    request.session.clear()
    return RedirectResponse("/setup", status_code=303)
