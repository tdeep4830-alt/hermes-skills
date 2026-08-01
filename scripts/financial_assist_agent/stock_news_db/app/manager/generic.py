"""
同 model 無關嘅通用 CRUD function，畀 CompanyManagerMixin / NewsManagerMixin
入面嗰啲 create_xxx / get_xxx / update_xxx / delete_xxx / list_xxx 方法內部共用，
避免每個 model 都寫多次一樣嘅邏輯。
"""
from __future__ import annotations

from typing import Any, Optional, Type, TypeVar

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


def create_obj(session: Session, model: Type[ModelT], **kwargs: Any) -> ModelT:
    obj = model(**kwargs)
    session.add(obj)
    session.flush()  # 即刻攞返 PK / server_default 嘅值 (例如 created_at)
    return obj


def get_obj(session: Session, model: Type[ModelT], pk: Any) -> Optional[ModelT]:
    """
    pk 可以係單一 primary key (例如 company_id=1)，
    亦可以係 tuple，用嚟查 composite primary key 嘅 table
    (例如 NewsCompanyLink 用 (news_id, company_id))。
    """
    return session.get(model, pk)


def update_obj(session: Session, model: Type[ModelT], pk: Any, **kwargs: Any) -> Optional[ModelT]:
    obj = session.get(model, pk)
    if obj is None:
        return None
    valid_columns = {c.key for c in inspect(model).mapper.column_attrs}
    for key, value in kwargs.items():
        if key not in valid_columns:
            raise AttributeError(f"{model.__name__} 無 '{key}' 呢個欄位")
        setattr(obj, key, value)
    session.flush()
    return obj


def delete_obj(session: Session, model: Type[ModelT], pk: Any) -> bool:
    obj = session.get(model, pk)
    if obj is None:
        return False
    session.delete(obj)
    return True


def list_obj(
    session: Session,
    model: Type[ModelT],
    *,
    limit: int = 100,
    offset: int = 0,
    order_by: Any = None,
    **filters: Any,
) -> list[ModelT]:
    """
    簡單等值篩選,例如 list_obj(session, Product, company_id=1)。
    要做範圍/模糊查詢 (日期區間、關鍵字 LIKE) 嘅話,
    用返 DatabaseManager 度啲專屬方法 (例如 search_news / get_news_for_company)。
    """
    valid_columns = {c.key for c in inspect(model).mapper.column_attrs}
    stmt = select(model)
    for key, value in filters.items():
        if key not in valid_columns:
            raise AttributeError(f"{model.__name__} 無 '{key}' 呢個欄位")
        stmt = stmt.where(getattr(model, key) == value)
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    stmt = stmt.limit(limit).offset(offset)
    return list(session.scalars(stmt).all())


def count_obj(session: Session, model: Type[ModelT], **filters: Any) -> int:
    valid_columns = {c.key for c in inspect(model).mapper.column_attrs}
    stmt = select(func.count()).select_from(model)
    for key, value in filters.items():
        if key not in valid_columns:
            raise AttributeError(f"{model.__name__} 無 '{key}' 呢個欄位")
        stmt = stmt.where(getattr(model, key) == value)
    return session.scalar(stmt) or 0


def model_to_dict(obj: Any) -> dict:
    """
    將一個 model instance 轉做純 dict —— 淨係欄位 (column)，
    唔包 relationship (例如 company.products)，避免喺 session 已經 close 咗嘅情況下
    觸發 lazy load 引致 DetachedInstanceError。
    """
    mapper = inspect(obj).mapper
    return {c.key: getattr(obj, c.key) for c in mapper.column_attrs}
