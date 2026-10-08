"""
File-format parsing for the Instance Management import, decoupled from
HTTP (no UploadFile) so BOTH the synchronous single-file route AND the
background multi-file worker can share one implementation.

Turns raw file bytes + a filename into a list of `ParsedSheet` — one per
worksheet (a CSV yields exactly one). Each ParsedSheet carries the
canonical-aligned rows plus its own header errors, and knows which file
and sheet it came from, which is what lets the worker report errors as
{file, sheet, row, messages[]} and track progress per sheet.

The actual header/row alignment rules are unchanged from the original
parser that lived in app/api/v1/instances.py — a sheet's header is
validated INDEPENDENTLY against the system's real expected columns
(never against another sheet), unknown/extra columns are ignored, rows
are re-aligned to the canonical `expected_labels` order, and each row
gets a human location label. The router now delegates here.
"""
import csv
import io
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from app.core.exceptions import ValidationError

# Accepted upload extensions -> the parser branch to use.
CSV_EXTENSIONS = (".csv",)
XLSX_EXTENSIONS = (".xlsx",)


@dataclass
class ParsedSheet:
    """One worksheet's parsed content, aligned to the canonical column
    order. `header_errors` is non-empty when the sheet's header is
    invalid (a required column missing) — in that case `rows` is empty
    and none of the sheet's data is emitted, mirroring the original
    all-or-nothing-per-sheet-header behavior.

    `row_labels[i]` is the human location for `rows[i]` (e.g.
    "Zone3, row 12" or "row 12"), and `row_numbers[i]` is the raw
    1-based line number within the sheet (header = row 1) used to tag
    structured errors."""

    file_name: str
    sheet_name: Optional[str]  # None for a single-sheet CSV
    headers: List[str]
    rows: List[List[object]] = field(default_factory=list)
    row_labels: List[str] = field(default_factory=list)
    row_numbers: List[int] = field(default_factory=list)
    header_errors: List[str] = field(default_factory=list)


def _norm_header(value: object) -> str:
    """Trimmed/lowercased/whitespace-collapsed — mirrors the service's
    header matching so parser-side and service-side column resolution
    agree."""
    return " ".join(str(value or "").strip().lower().split())


def _validate_sheet_header(
    sheet_name: Optional[str],
    header_values: List[object],
    expected_labels: List[str],
    required_labels: List[str],
) -> Tuple[Optional[List[int]], Optional[str]]:
    """Validate ONE sheet's/CSV's header against the REAL expected
    columns. Returns (col_map, error). col_map[i] = source index in this
    header for the i-th expected column (None if an absent OPTIONAL
    column); error is a message if a REQUIRED column is missing. Unknown
    columns in the file are ignored/dropped."""
    exp_norm = [_norm_header(label) for label in expected_labels]
    req_norm = {_norm_header(label) for label in required_labels}
    present = {}
    for idx, cell in enumerate(header_values):
        n = _norm_header(cell)
        if n:
            present[n] = idx

    missing_required = [
        expected_labels[i]
        for i, n in enumerate(exp_norm)
        if n in req_norm and n not in present
    ]
    if missing_required:
        where = f"{sheet_name}: " if sheet_name else ""
        return None, (
            f"{where}missing required column(s): "
            + ", ".join(repr(m) for m in missing_required)
            + f". Required: {', '.join(required_labels)}."
        )

    col_map = [present.get(n) for n in exp_norm]
    return col_map, None


def _row_label(sheet: Optional[str], line: int) -> str:
    """"Zone3, row 12" for a multi-sheet workbook, "row 12" for a CSV."""
    return f"{sheet}, row {line}" if sheet else f"row {line}"


def _is_blank_row(values) -> bool:
    return not any(str(v).strip() for v in values if v is not None)


def parse_csv(
    file_name: str,
    content: bytes,
    expected_labels: List[str],
    required_labels: List[str],
) -> List[ParsedSheet]:
    """A CSV is a single, unnamed sheet (sheet_name=None). Decodes
    utf-8-sig (strips Excel's BOM), validates the header, and re-aligns
    rows to the canonical order."""
    sheet = ParsedSheet(file_name=file_name, sheet_name=None, headers=list(expected_labels))

    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    all_rows = [row for row in reader]
    if not all_rows:
        return [sheet]

    header_values = [c.strip() for c in all_rows[0]]
    col_map, error = _validate_sheet_header(None, header_values, expected_labels, required_labels)
    if error is not None:
        sheet.header_errors = [error]
        return [sheet]

    # Enumerate over the ORIGINAL file lines so the label's row number
    # matches what the user sees (header = line 1).
    for line_no, row in enumerate(all_rows[1:], start=2):
        if any(str(c).strip() for c in row):
            aligned = [
                (row[src] if (src is not None and src < len(row)) else None)
                for src in col_map
            ]
            sheet.rows.append(aligned)
            sheet.row_labels.append(_row_label(None, line_no))
            sheet.row_numbers.append(line_no)
    return [sheet]


def parse_xlsx(
    file_name: str,
    content: bytes,
    expected_labels: List[str],
    required_labels: List[str],
) -> List[ParsedSheet]:
    """Only the FIRST non-empty worksheet is processed; any remaining
    sheets in the workbook are IGNORED entirely (not validated, not
    imported, not reported). Fully-empty leading sheets are skipped so
    "first sheet" means the first sheet that actually has content. The
    chosen sheet's header is validated and its rows re-aligned to the
    canonical column order; a bad header emits no rows (header_errors is
    set). Returns a list with at most one ParsedSheet, so the rest of the
    pipeline (progress tree, counters, errors) is naturally single-sheet."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    parsed: List[ParsedSheet] = []

    try:
        for ws in wb.worksheets:
            sheet = ParsedSheet(file_name=file_name, sheet_name=ws.title, headers=list(expected_labels))
            sheet_iter = enumerate(ws.iter_rows(values_only=True), start=1)

            header_values = None
            for _line, row in sheet_iter:
                values = list(row)
                if _is_blank_row(values):
                    continue
                header_values = values
                break
            if header_values is None:
                # Empty sheet — skip entirely and keep looking for the
                # first sheet that has content.
                continue

            col_map, error = _validate_sheet_header(ws.title, header_values, expected_labels, required_labels)
            if error is not None:
                sheet.header_errors = [error]
                parsed.append(sheet)
                # First non-empty sheet decided (header invalid) — ignore
                # all remaining sheets.
                break

            for line, row in sheet_iter:
                values = list(row)
                if _is_blank_row(values):
                    continue
                aligned = [
                    (values[src] if (src is not None and src < len(values)) else None)
                    for src in col_map
                ]
                sheet.rows.append(aligned)
                sheet.row_labels.append(_row_label(ws.title, line))
                sheet.row_numbers.append(line)
            parsed.append(sheet)
            # Only the first non-empty sheet is imported — ignore the rest.
            break
    finally:
        wb.close()

    return parsed


def file_type_for(file_name: str) -> str:
    """'csv' | 'xlsx' from the filename, or raise the same
    ValidationError the route used for an unsupported type."""
    name = (file_name or "").lower()
    if name.endswith(CSV_EXTENSIONS):
        return "csv"
    if name.endswith(XLSX_EXTENSIONS):
        return "xlsx"
    raise ValidationError(
        f"Unsupported file type: {file_name!r}. Only .csv and .xlsx are accepted.",
        code="IMPORT_UNSUPPORTED_FILE_TYPE",
    )


def parse_bytes(
    file_name: str,
    content: bytes,
    expected_labels: List[str],
    required_labels: List[str],
) -> List[ParsedSheet]:
    """Dispatch on extension and return one ParsedSheet per sheet. Raises
    ValidationError(IMPORT_UNSUPPORTED_FILE_TYPE) for anything but
    .csv/.xlsx."""
    kind = file_type_for(file_name)
    if kind == "csv":
        return parse_csv(file_name, content, expected_labels, required_labels)
    return parse_xlsx(file_name, content, expected_labels, required_labels)
