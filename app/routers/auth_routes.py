from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import flash, hash_password, verify_csrf, verify_password
from ..database import get_db
from ..models import User
from ..templating import templates

router = APIRouter()


@router.get("/setup")
def setup_page(request: Request):
    return templates.TemplateResponse(request, "setup.html")


@router.post("/setup")
def do_setup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    if db.query(User.id).first() is not None:
        return RedirectResponse("/login", status_code=303)

    username = username.strip()
    error = None
    if not username:
        error = "Username is required."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif password != confirm:
        error = "Passwords do not match."
    if error:
        flash(request, error, "error")
        return templates.TemplateResponse(request, "setup.html", {"form_username": username})

    user = User(username=username, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    flash(request, f"Welcome, {user.username}! Your account was created.", "success")
    return RedirectResponse("/spools", status_code=303)


@router.get("/login")
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/spools", status_code=303)
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
def do_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    user = db.query(User).filter(User.username == username.strip()).first()
    if not user or not verify_password(password, user.hashed_password):
        flash(request, "Invalid username or password.", "error")
        return templates.TemplateResponse(request, "login.html", {"form_username": username})
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    return RedirectResponse("/spools", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(None)):
    verify_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
