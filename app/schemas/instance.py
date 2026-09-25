"""
Pydantic response schema for the Instance API (read-only, API #1 of the
Bin Series gap analysis).

Field names are camelCase, matching every other response schema in this
project (BinRecordResponse, UserResponse, ...). `updatedBy` is resolved
server-side to the actor's name (never a raw user id) — same convention
as BinRecordResponse.updatedBy. `issuerIds` (the frontend's Instance
Management shape) is deliberately NOT exposed here: this task explicitly
scopes out any issuer relationship, and GET /api/v1/instances only needs
to support the Bin Series dropdown, not the full Instance Management
page.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class InstanceResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    status: str
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None
