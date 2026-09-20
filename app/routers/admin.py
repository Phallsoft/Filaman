import datetime
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..auth import flash, hash_password, require_admin, verify_csrf
from ..database import get_db, reset_db
from ..models import Spool, User
from ..services import backup as backup_svc
from ..services.users import create_user, delete_user, validate_password
from ..templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

RESET_PHRASE = "Yes, Really Reset The Database"


@router.get("")
def admin_page(request: Request, db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id).all()
    spool_counts = dict(db.query(Spool.user_id, func.count(Spool.id)).group_by(Spool.user_id).all())
    return templates.TemplateResponse(
        request, "admin.html",
        {"reset_phrase": RESET_PHRASE, "users": users, "spool_counts": spool_counts},
    )


@router.post("/users")
def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    try:
        user = create_user(db, username, password)
    except ValueError as e:
        flash(request, str(e), "error")
        return RedirectResponse("/admin", status_code=303)
    db.commit()
    flash(request, f"User '{user.username}' created.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/password")
def reset_user_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    target = db.get(User, user_id)
    err = validate_password(new_password)
    if target is None:
        flash(request, "User not found.", "error")
    elif err:
        flash(request, err, "error")
    else:
        target.hashed_password = hash_password(new_password)
        db.commit()
        flash(request, f"Password reset for '{target.username}'.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/delete")
def remove_user(
    request: Request,
    user_id: int,
    confirm_text: str = Form(""),
    csrf_token: str = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    target = db.get(User, user_id)
    if target is None:
        flash(request, "User not found.", "error")
    elif target.id == admin.id:
        flash(request, "You cannot delete your own account.", "error")
    elif confirm_text.strip() != target.username:
        flash(request, f"Delete cancelled — type the username '{target.username}' to confirm.", "error")
    else:
        delete_user(db, target)
        db.commit()
        flash(request, f"User '{target.username}' and all their data were deleted.", "success")
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
