"""
Shared pytest fixtures.

Only a TestClient fixture at this stage — no database fixtures yet, since
no models/tables exist (Part 4 is foundation-only). Database-backed
integration test fixtures are added alongside the models in a later part.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
