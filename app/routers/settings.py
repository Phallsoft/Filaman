from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import current_user, flash, hash_password, verify_csrf, verify_password
from ..database import get_db
from ..models import User
from ..services.users import validate_password
from ..templating import templates

router = APIRouter(prefix="/settings")


@router.get("")
def settings_page(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse(request, "settings.html")


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    err = validate_password(new_password)
    if not verify_password(current_password, user.hashed_password):
        flash(request, "Current password is incorrect.", "error")
    elif err:
        flash(request, err, "error")
    elif new_password != confirm_password:
        flash(request, "New passwords do not match.", "error")
    else:
        user.hashed_password = hash_password(new_password)
        db.commit()
        flash(request, "Password changed.", "success")
    return RedirectResponse("/settings", status_code=303)
