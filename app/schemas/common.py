"""
Shared response envelopes used across every domain's schemas, per the
API response design (Part 3 §13):

- PaginatedResponse[T]: the one consistent shape for every list endpoint
  ({"items": [...], "total", "page", "pageSize"}) — defined once here as
  a generic rather than redefined per domain (e.g. no separate
  BinListResponse/MerchantListResponse classes).
- ErrorResponse / ErrorDetail: mirrors the envelope already implemented
  by app/core/error_handlers.py. Declared here mainly so it can be
  referenced in OpenAPI response documentation; the handlers build the
  actual JSON independently.
"""
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    pageSize: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
