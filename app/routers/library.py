from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..auth import current_user, flash, verify_csrf
from ..database import get_db, get_owned
from ..models import LibraryModel, LibraryTag, User, library_model_tags
from ..services import library as lib
from ..services.library_search import PUBLIC_COLUMNS, reindex, search_ids
from ..services.library_taxonomy import (
    category_path,
    descendant_ids,
    get_or_create_tags,
    parse_tag_names,
    tree_rows,
    user_categories,
)
from ..templating import templates

router = APIRouter(prefix="/models")

PAGE_SIZE = 60
FORM_FIELDS = ("title", "source_url", "platform", "author", "license", "description", "print_settings")


def _user_tags(db: Session, user_id: int) -> list[tuple[LibraryTag, int]]:
    """The user's tags with how many models use each, by name."""
    rows = (
        db.query(LibraryTag, func.count(library_model_tags.c.model_id))
        .outerjoin(library_model_tags, library_model_tags.c.tag_id == LibraryTag.id)
        .filter(LibraryTag.user_id == user_id)
        .group_by(LibraryTag.id)
        .order_by(func.lower(LibraryTag.name))
        .all()
    )
    return rows


def _category_rows(db: Session, user_id: int) -> tuple[list, list[dict], dict]:
    cats = user_categories(db, user_id)
    counts = dict(
        db.query(LibraryModel.category_id, func.count(LibraryModel.id))
        .filter(LibraryModel.user_id == user_id)
        .group_by(LibraryModel.category_id)
        .all()
    )
    return cats, tree_rows(cats, counts), counts


def _form_context(db: Session, user: User, model: LibraryModel | None, values: dict | None = None, error: str | None = None) -> dict:
    cats, rows, _ = _category_rows(db, user.id)
    if values is None:
        values = {f: (getattr(model, f) or "") if model else "" for f in FORM_FIELDS}
        values["category_id"] = str(model.category_id or "") if model else ""
        values["tags"] = ", ".join(t.name for t in model.tags) if model else ""
        values["new_category"] = ""
    return {
        "model": model,
        "values": values,
        "error": error,
        "category_rows": rows,
        "all_tags": [t for t, _ in _user_tags(db, user.id)],
    }


@router.get("")
def library(
    request: Request,
    q: str = "",
    cat: str = "",
    tag: list[str] = Query(default=[]),
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    cats, rows, counts = _category_rows(db, user.id)
    by_id = {c.id: c for c in cats}
    query = db.query(LibraryModel).filter(LibraryModel.user_id == user.id)

    selected_cat = None
    if cat == "none":
        query = query.filter(LibraryModel.category_id.is_(None))
    elif cat.isdigit() and int(cat) in by_id:
        selected_cat = by_id[int(cat)]
        query = query.filter(LibraryModel.category_id.in_(descendant_ids(cats, selected_cat.id)))
    else:
        cat = ""

    user_tags = _user_tags(db, user.id)
    tags_by_id = {t.id: t for t, _ in user_tags}
    active_tag_ids = [int(t) for t in tag if t.isdigit() and int(t) in tags_by_id]
    for tag_id in active_tag_ids:
        query = query.filter(LibraryModel.tags.any(LibraryTag.id == tag_id))

    q = q.strip()
    ids = search_ids(db, q)
    if ids is not None:
        query = query.filter(LibraryModel.id.in_(ids))

    total = query.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    models = (
        query.options(selectinload(LibraryModel.tags), selectinload(LibraryModel.images), selectinload(LibraryModel.files))
        .order_by(LibraryModel.updated_at.desc(), LibraryModel.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    def url_with(**overrides) -> str:
        state = {"q": q, "cat": cat, "tag": [str(t) for t in active_tag_ids], "page": ""}
        state.update(overrides)
        params = [(k, v) for k, vals in state.items() for v in (vals if isinstance(vals, list) else [vals]) if v not in ("", None)]
        return "/models" + ("?" + urlencode(params) if params else "")

    def toggle_tag(tag_id: int) -> str:
        current = [str(t) for t in active_tag_ids]
        nxt = [t for t in current if t != str(tag_id)] if str(tag_id) in current else current + [str(tag_id)]
        return url_with(tag=nxt)

    return templates.TemplateResponse(
        request,
        "library/list.html",
        {
            "models": models,
            "total": total,
            "all_count": sum(counts.values()),
            "uncategorized_count": counts.get(None, 0),
            "category_rows": rows,
            "selected_cat": selected_cat,
            "selected_path": category_path(by_id, selected_cat.id) if selected_cat else "",
            "cat": cat,
            "q": q,
            "user_tags": user_tags,
            "active_tag_ids": active_tag_ids,
            "page": page,
            "pages": pages,
            "url_with": url_with,
            "toggle_tag": toggle_tag,
            "current_url": url_with(page=page if page > 1 else ""),
        },
    )


@router.post("/quick")
def quick_add(
    request: Request,
    url: str = Form(""),
    title: str = Form(""),
    category_id: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    model = LibraryModel(user_id=user.id)
    error = lib.apply_fields(model, title=title, source_url=url, platform=None, author=None, license=None,
                             description=None, print_settings=None)
    cat_id, _ = lib.resolve_category(db, user.id, category_id, None)
    back = f"/models?cat={cat_id}" if cat_id else "/models"
    if error:
        flash(request, error, "error")
        return RedirectResponse(back, status_code=303)
    model.category_id = cat_id
    db.add(model)
    db.flush()
    reindex(db, [model.id])
    db.commit()
    flash(request, f"Added “{model.title}”.", "success")
    return RedirectResponse(back, status_code=303)


@router.get("/new")
def new_model(request: Request, cat: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    ctx = _form_context(db, user, None)
    ctx["values"]["category_id"] = cat if cat.isdigit() else ""
    return templates.TemplateResponse(request, "library/form.html", ctx)


def _submitted_values(title, source_url, platform, author, license, description, print_settings, category_id, new_category, tags) -> dict:
    return {
        "title": title or "", "source_url": source_url or "", "platform": platform or "", "author": author or "",
        "license": license or "", "description": description or "", "print_settings": print_settings or "",
        "category_id": category_id or "", "new_category": new_category or "", "tags": tags or "",
    }


@router.post("")
async def create_model(
    request: Request,
    title: str = Form(""),
    source_url: str = Form(""),
    platform: str = Form(""),
    author: str = Form(""),
    license: str = Form(""),
    description: str = Form(""),
    print_settings: str = Form(""),
    category_id: str = Form(""),
    new_category: str = Form(""),
    tags: str = Form(""),
    image_url: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    values = _submitted_values(title, source_url, platform, author, license, description, print_settings,
                               category_id, new_category, tags)
    form = await request.form()
    return await run_in_threadpool(
        _save_model, request, db, user, None, values, _uploads(form, "files"), _uploads(form, "images"), image_url
    )


def _save_model(request: Request, db: Session, user: User, model: LibraryModel | None, values: dict,
                files: list, images: list, image_url: str):
    """Create (model=None) or update a model. Runs in a worker thread.

    Order matters: validate in memory, stage uploads (slow: copying, fetching, image work)
    while holding no database lock, then do every write in one short transaction.
    """
    creating = model is None
    if creating:
        model = LibraryModel(user_id=user.id)
    error = lib.apply_fields(model, **{f: values[f] for f in FORM_FIELDS})
    staged = lib.StagedUploads() if error else lib.stage_uploads(files, images, image_url)
    cat_id, cat_error = (None, None) if error else lib.resolve_category(db, user.id, values["category_id"],
                                                                        values["new_category"])
    if error or cat_error:
        staged.discard()
        db.rollback()
        if not creating:
            db.refresh(model)
        return templates.TemplateResponse(
            request, "library/form.html",
            _form_context(db, user, None if creating else model, values, error or cat_error), status_code=400,
        )
    model.category_id = cat_id
    if creating:
        db.add(model)
        db.flush()
    lib.set_tags(db, model, parse_tag_names(values["tags"]))
    lib.attach_staged(db, model, staged)
    reindex(db, [model.id])
    db.commit()
    for msg in staged.errors:
        flash(request, msg, "error")
    flash(request, f"Added “{model.title}”." if creating else "Model saved.", "success")
    return RedirectResponse(f"/models/{model.id}", status_code=303)


def _uploads(form, name: str) -> list[UploadFile]:
    """Real uploads only: browsers send an empty part (parsed as "") for an untouched file input."""
    return [f for f in form.getlist(name) if isinstance(f, UploadFile) and f.filename]


def _owned_or_404(db: Session, model_id: int, user: User) -> LibraryModel:
    model = get_owned(db, LibraryModel, model_id, user.id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return model


@router.get("/{model_id:int}")
def model_detail(request: Request, model_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    model = lib.get_viewable(db, model_id, user)
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    is_owner = model.user_id == user.id
    cats, rows, _ = _category_rows(db, user.id)
    my_copy = None
    if not is_owner:
        my_copy = (
            db.query(LibraryModel)
            .filter(LibraryModel.user_id == user.id, LibraryModel.copied_from_id == model.id)
            .first()
        )
    return templates.TemplateResponse(
        request,
        "library/detail.html",
        {
            "model": model,
            "is_owner": is_owner,
            "category_path": category_path({c.id: c for c in cats}, model.category_id) if is_owner else "",
            "category_rows": rows,
            "my_copy": my_copy,
            "file_exists": {f.id: lib.file_store.exists(f.stored) for f in model.files},
        },
    )


@router.get("/{model_id:int}/edit")
def edit_model(request: Request, model_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    model = _owned_or_404(db, model_id, user)
    return templates.TemplateResponse(request, "library/form.html", _form_context(db, user, model))


@router.post("/{model_id:int}")
async def update_model(
    request: Request,
    model_id: int,
    title: str = Form(""),
    source_url: str = Form(""),
    platform: str = Form(""),
    author: str = Form(""),
    license: str = Form(""),
    description: str = Form(""),
    print_settings: str = Form(""),
    category_id: str = Form(""),
    new_category: str = Form(""),
    tags: str = Form(""),
    image_url: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    model = _owned_or_404(db, model_id, user)
    values = _submitted_values(title, source_url, platform, author, license, description, print_settings,
                               category_id, new_category, tags)
    form = await request.form()
    return await run_in_threadpool(
        _save_model, request, db, user, model, values, _uploads(form, "files"), _uploads(form, "images"), image_url
    )


@router.post("/{model_id:int}/delete")
def delete_model(
    request: Request,
    model_id: int,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    model = _owned_or_404(db, model_id, user)
    title = model.title
    lib.delete_model(db, model)
    db.commit()
    flash(request, f"Deleted “{title}”.", "success")
    return RedirectResponse("/models", status_code=303)


@router.post("/{model_id:int}/share")
def share_model(
    request: Request,
    model_id: int,
    shared: str = Form("0"),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    model = _owned_or_404(db, model_id, user)
    model.is_shared = shared == "1"
    db.commit()
    flash(request, "Shared with all users." if model.is_shared else "No longer shared.", "success")
    return RedirectResponse(f"/models/{model.id}", status_code=303)


@router.get("/shared")
def shared_library(
    request: Request,
    q: str = "",
    owner: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    query = db.query(LibraryModel).filter(LibraryModel.is_shared.is_(True))
    owners = (
        db.query(User)
        .join(LibraryModel, LibraryModel.user_id == User.id)
        .filter(LibraryModel.is_shared.is_(True))
        .distinct()
        .order_by(func.lower(User.username))
        .all()
    )
    owner_id = int(owner) if owner.isdigit() else None
    if owner_id:
        query = query.filter(LibraryModel.user_id == owner_id)
    q = q.strip()
    ids = search_ids(db, q, PUBLIC_COLUMNS)
    if ids is not None:
        query = query.filter(LibraryModel.id.in_(ids))
    total = query.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    models = (
        query.options(selectinload(LibraryModel.images), selectinload(LibraryModel.files), selectinload(LibraryModel.owner))
        .order_by(LibraryModel.updated_at.desc(), LibraryModel.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )
    copies = dict(
        db.query(LibraryModel.copied_from_id, LibraryModel.id)
        .filter(LibraryModel.user_id == user.id, LibraryModel.copied_from_id.in_([m.id for m in models]))
        .all()
    )

    def url_with(**overrides) -> str:
        state = {"q": q, "owner": str(owner_id or ""), "page": ""}
        state.update({k: str(v) for k, v in overrides.items()})
        params = [(k, v) for k, v in state.items() if v]
        return "/models/shared" + ("?" + urlencode(params) if params else "")

    return templates.TemplateResponse(
        request,
        "library/shared.html",
        {
            "models": models, "total": total, "q": q, "owner_id": owner_id, "owners": owners,
            "copies": copies, "page": page, "pages": pages, "url_with": url_with, "user_id": user.id,
        },
    )


@router.post("/{model_id:int}/copy")
def copy_shared_model(
    request: Request,
    model_id: int,
    category_id: str = Form(""),
    new_category: str = Form(""),
    tags: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    src = lib.get_viewable(db, model_id, user)
    if src is None or src.user_id == user.id:
        raise HTTPException(status_code=404, detail="Model not found")
    cat_id, cat_error = lib.resolve_category(db, user.id, category_id, new_category)
    if cat_error:
        flash(request, cat_error, "error")
    copy = lib.copy_model(db, src, user, cat_id, parse_tag_names(tags))
    db.commit()
    flash(request, f"Added “{copy.title}” to your library.", "success")
    return RedirectResponse(f"/models/{copy.id}", status_code=303)


def _safe_next(target: str | None) -> str:
    target = target or ""
    if target == "/models" or target.startswith(("/models?", "/models/")):
        return target
    return "/models"


@router.post("/bulk")
def bulk_action(
    request: Request,
    action: str = Form(""),
    ids: list[int] = Form(default=[]),
    category_id: str = Form(""),
    tag: str = Form(""),
    next: str = Form("/models"),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    back = RedirectResponse(_safe_next(next), status_code=303)
    models = db.query(LibraryModel).filter(LibraryModel.user_id == user.id, LibraryModel.id.in_(ids)).all() if ids else []
    if not models:
        flash(request, "Select at least one model first.", "error")
        return back
    n = len(models)
    noun = f"{n} model{'' if n == 1 else 's'}"
    touched = [m.id for m in models]

    if action == "move":
        cat_id, _ = lib.resolve_category(db, user.id, category_id, None)
        for m in models:
            m.category_id = cat_id
        message = f"Moved {noun}."
    elif action in ("add_tag", "remove_tag"):
        names = parse_tag_names(tag)
        if not names:
            flash(request, "Enter a tag name.", "error")
            return back
        if action == "add_tag":
            the_tag = get_or_create_tags(db, user.id, names[:1])[0]
        else:
            the_tag = (
                db.query(LibraryTag)
                .filter(LibraryTag.user_id == user.id, func.lower(LibraryTag.name) == names[0].lower())
                .first()
            )
        if the_tag is None:
            flash(request, f"No tag named “{names[0]}”.", "error")
            return back
        for m in models:
            if action == "add_tag" and the_tag not in m.tags:
                m.tags.append(the_tag)
            elif action == "remove_tag" and the_tag in m.tags:
                m.tags.remove(the_tag)
        message = f"{'Tagged' if action == 'add_tag' else 'Untagged'} {noun} “{the_tag.name}”."
    elif action in ("share", "unshare"):
        for m in models:
            m.is_shared = action == "share"
        message = f"{'Shared' if action == 'share' else 'Unshared'} {noun}."
    elif action == "delete":
        for m in models:
            lib.delete_model(db, m)
        touched = []
        message = f"Deleted {noun}."
    else:
        flash(request, "Unknown action.", "error")
        return back

    reindex(db, touched)
    db.commit()
    flash(request, message, "success")
    return back
