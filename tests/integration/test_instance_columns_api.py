"""
API tests for Instance Management column ORDERING (drag-and-drop):
  GET   /api/v1/instances/columns          -> {definitions, layout}
  POST  /api/v1/instances/columns
  PATCH /api/v1/instances/columns/reorder

Focus: the free reorder of the WHOLE table (built-in + custom columns) via
a single ordered list of column KEYS, and the fact that any arrangement
(a custom column before the Instance built-in, built-ins reordered among
themselves) is persisted and reflected back by /columns/layout.

Uses `api_client` (tests/integration/conftest.py): every request runs
inside the test's rolled-back transaction, so these create no persistent
data. Custom columns get unique labels per test to avoid the
instance_columns UNIQUE(label/key) constraints; the reorder endpoint
re-anchors built-ins too, which is exercised directly here.

The five built-in keys, in their historical default order, per
app.models.instance.BUILTIN_COLUMN_DEFS.
"""
BUILTIN_KEYS_DEFAULT = ["name", "ticketNumber", "issuerCount", "status", "updatedBy"]

BASE = "/api/v1/instances/columns"


def _columns(api_client):
    """GET /columns now returns {definitions, layout} in one payload."""
    res = api_client.get(BASE)
    assert res.status_code == 200, res.text
    return res.json()


def _layout_keys(api_client):
    return [c["key"] for c in _columns(api_client)["layout"]]


def _create_column(api_client, label, after_key=None):
    """Create a custom column. `after_key` mirrors what the Add Column
    modal sends — it anchors the new column after that key. The modal
    always passes the current last column's key so a new column lands at
    the END of the table; tests that want that pass after_key explicitly."""
    body = {"label": label, "type": "text"}
    if after_key is not None:
        body["afterKey"] = after_key
    res = api_client.post(BASE, json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_columns_returns_definitions_and_layout(api_client):
    body = _columns(api_client)
    assert set(body.keys()) == {"definitions", "layout"}
    # No custom columns yet -> empty definitions, built-ins-only layout.
    assert body["definitions"] == []
    assert [c["key"] for c in body["layout"]] == BUILTIN_KEYS_DEFAULT
    # Layout carries the builtin flag; definitions are custom-only.
    assert all(c["builtin"] for c in body["layout"])


def test_definitions_lists_only_custom_columns(api_client):
    col = _create_column(api_client, "Test Region Z")
    body = _columns(api_client)
    keys = [c["key"] for c in body["definitions"]]
    assert keys == [col["key"]]  # only the custom column, no built-ins


def test_layout_defaults_to_builtin_order(api_client):
    assert _layout_keys(api_client) == BUILTIN_KEYS_DEFAULT


def test_new_column_lands_at_end(api_client):
    # Modal anchors a new column after the current last column (updatedBy).
    col = _create_column(api_client, "Test Region A", after_key=BUILTIN_KEYS_DEFAULT[-1])
    keys = _layout_keys(api_client)
    # Built-ins unchanged, custom appended last.
    assert keys[: len(BUILTIN_KEYS_DEFAULT)] == BUILTIN_KEYS_DEFAULT
    assert keys[-1] == col["key"]


def test_reorder_moves_custom_before_first_builtin(api_client):
    col = _create_column(api_client, "Test Region B")
    # Move the custom column to the very front.
    new_order = [col["key"]] + BUILTIN_KEYS_DEFAULT
    res = api_client.patch(f"{BASE}/reorder", json={"orderedKeys": new_order})
    assert res.status_code == 200, res.text
    assert [c["key"] for c in res.json()] == new_order
    # And it persists on a fresh layout read.
    assert _layout_keys(api_client) == new_order


def test_reorder_builtins_among_themselves(api_client):
    # Move Status to the front of the built-ins (no custom columns).
    reordered = ["status", "name", "ticketNumber", "issuerCount", "updatedBy"]
    res = api_client.patch(f"{BASE}/reorder", json={"orderedKeys": reordered})
    assert res.status_code == 200, res.text
    assert _layout_keys(api_client) == reordered


def test_reorder_interleaves_custom_between_builtins(api_client):
    col = _create_column(api_client, "Test Region C")
    # name, <custom>, ticketNumber, issuerCount, status, updatedBy
    new_order = ["name", col["key"], "ticketNumber", "issuerCount", "status", "updatedBy"]
    res = api_client.patch(f"{BASE}/reorder", json={"orderedKeys": new_order})
    assert res.status_code == 200, res.text
    assert _layout_keys(api_client) == new_order

    # The custom column's afterKey should now anchor to its left neighbor.
    definitions = _columns(api_client)["definitions"]
    moved = next(c for c in definitions if c["key"] == col["key"])
    assert moved["afterKey"] == "name"


def test_reorder_rejects_missing_key(api_client):
    # Drop a built-in key -> mismatch.
    bad = BUILTIN_KEYS_DEFAULT[:-1]
    res = api_client.patch(f"{BASE}/reorder", json={"orderedKeys": bad})
    assert res.status_code == 422, res.text


def test_reorder_rejects_unknown_key(api_client):
    bad = BUILTIN_KEYS_DEFAULT + ["nope_not_a_column"]
    res = api_client.patch(f"{BASE}/reorder", json={"orderedKeys": bad})
    assert res.status_code == 422, res.text


def test_reorder_rejects_duplicate_key(api_client):
    bad = ["name"] + BUILTIN_KEYS_DEFAULT  # 'name' twice
    res = api_client.patch(f"{BASE}/reorder", json={"orderedKeys": bad})
    assert res.status_code == 422, res.text
