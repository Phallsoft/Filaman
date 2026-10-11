"""Model library category tree and tag management pages."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import current_user, flash, verify_csrf
from ..database import get_db, get_owned
from ..models import LibraryCategory, LibraryModel, LibraryTag, User, library_model_tags
from ..services import library_taxonomy as tx
from ..services.library_search import reindex
from ..templating import templates

router = APIRouter(prefix="/models")


def _parent_id(raw: str) -> int | None:
    return int(raw) if raw and raw.isdigit() else None


# ---- categories -------------------------------------------------------------

@router.get("/categories")
def categories(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    cats = tx.user_categories(db, user.id)
    counts = dict(
        db.query(LibraryModel.category_id, func.count(LibraryModel.id))
        .filter(LibraryModel.user_id == user.id)
        .group_by(LibraryModel.category_id)
        .all()
    )
    rows = tx.tree_rows(cats, counts)
    # A category may not move under itself or its descendants.
    blocked = {row["category"].id: tx.descendant_ids(cats, row["category"].id) for row in rows}
    return templates.TemplateResponse(
        request, "library/categories.html", {"rows": rows, "blocked": blocked, "direct_counts": counts}
    )


@router.post("/categories")
def create_category(
    request: Request,
    name: str = Form(""),
    parent_id: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    try:
        cat = tx.create_category(db, user.id, name, _parent_id(parent_id))
        db.commit()
        flash(request, f"Category “{cat.name}” added.", "success")
    except tx.TaxonomyError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse("/models/categories", status_code=303)


def _owned_category_or_404(db: Session, cat_id: int, user: User) -> LibraryCategory:
    cat = get_owned(db, LibraryCategory, cat_id, user.id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


@router.post("/categories/{cat_id:int}")
def update_category(
    request: Request,
    cat_id: int,
    name: str = Form(""),
    parent_id: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    cat = _owned_category_or_404(db, cat_id, user)
    try:
        tx.move_category(db, cat, _parent_id(parent_id))
        tx.rename_category(db, cat, name)
        reindex(db, tx.affected_model_ids_for_categories(db, [cat.id]))
        db.commit()
        flash(request, "Category saved.", "success")
    except tx.TaxonomyError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse("/models/categories", status_code=303)


@router.post("/categories/{cat_id:int}/delete")
def delete_category(
    request: Request,
    cat_id: int,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    cat = _owned_category_or_404(db, cat_id, user)
    affected = tx.affected_model_ids_for_categories(db, [cat.id])
    name = cat.name
    tx.delete_category(db, cat)
    reindex(db, affected)
    db.commit()
    flash(request, f"Category “{name}” deleted. Its models are now Uncategorized; subcategories moved up.", "success")
    return RedirectResponse("/models/categories", status_code=303)


# ---- tags -------------------------------------------------------------------

@router.get("/tags")
def tags(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = (
        db.query(LibraryTag, func.count(library_model_tags.c.model_id))
        .outerjoin(library_model_tags, library_model_tags.c.tag_id == LibraryTag.id)
        .filter(LibraryTag.user_id == user.id)
        .group_by(LibraryTag.id)
        .order_by(func.lower(LibraryTag.name))
        .all()
    )
    return templates.TemplateResponse(request, "library/tags.html", {"rows": rows})


def _owned_tag_or_404(db: Session, tag_id: int, user: User) -> LibraryTag:
    tag = get_owned(db, LibraryTag, tag_id, user.id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag


@router.post("/tags/{tag_id:int}")
def rename_tag(
    request: Request,
    tag_id: int,
    name: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    tag = _owned_tag_or_404(db, tag_id, user)
    affected = tx.tag_model_ids(db, tag.id)
    try:
        survivor = tx.rename_tag(db, tag, name)
        reindex(db, affected)
        db.commit()
        merged = survivor.id != tag_id
        flash(request, f"Merged into “{survivor.name}”." if merged else "Tag renamed.", "success")
    except tx.TaxonomyError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse("/models/tags", status_code=303)


@router.post("/tags/{tag_id:int}/delete")
def delete_tag(
    request: Request,
    tag_id: int,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    verify_csrf(request, csrf_token)
    tag = _owned_tag_or_404(db, tag_id, user)
    affected = tx.tag_model_ids(db, tag.id)
    name = tag.name
    tx.delete_tag(db, tag)
    reindex(db, affected)
    db.commit()
    flash(request, f"Tag “{name}” deleted.", "success")
    return RedirectResponse("/models/tags", status_code=303)
