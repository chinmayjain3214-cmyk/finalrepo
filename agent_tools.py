import copy
import difflib
import json
import re
from datetime import datetime
from pathlib import Path

from checks import check_line_math, check_vendor
from db_store import save_log
from run_invoices import get_invoice_data
from tools import (
    find_po,
    load_saved_goods_receipts,
    load_saved_purchase_orders,
)
from rules import evaluate_invoice
# Reference data with the current department budgets (dataset figures plus
# any change Finance made), so the budget check uses the live numbers.
from budget_store import load_live_reference_data as load_reference_data

INVOICE_DIR = Path(__file__).resolve().parent / "invoices"
LOG_FILE = Path(__file__).resolve().parent / "agent_log.jsonl"

_review_reasons = {}
_dataset_results = {}
_dataset_history = {}
_tool_results = {}
_reference_data_cache = None
_db_warning_shown = False

_CACHE_MISS = object()


def _cache_key(tool_name, file_name, extra=None):
    return (
        str(file_name),
        tool_name,
        str(extra) if extra is not None else "",
    )


def _get_cached(key):
    value = _tool_results.get(key, _CACHE_MISS)

    if value is _CACHE_MISS:
        return _CACHE_MISS

    return copy.deepcopy(value)


def _store_cached(key, value):
    _tool_results[key] = copy.deepcopy(value)
    return copy.deepcopy(value)


def _first_value(data, *keys):
    for key in keys:
        value = data.get(key)

        if value is not None and value != "":
            return value

    return None


def _invoice_po_reference(data):
    """Extract the PO reference from the actual invoice data."""

    if not isinstance(data, dict):
        return None

    value = _first_value(
        data,
        "po_reference",
        "po_number",
        "purchase_order",
        "purchase_order_reference",
        "reference_po",
        "purchase_reference",
    )

    if value is None:
        details = data.get("reference_details")

        if isinstance(details, dict):
            reference_type = str(
                details.get("reference_type") or ""
            ).lower()

            if (
                "purchase order" in reference_type
                or reference_type == "po"
            ):
                value = details.get("customer_order")

    return str(value).strip() if value else None

def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None

def _normalize_po(value):
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value or "").lower(),
    )


def _merge_saved_procurement_records(reference):
    """Include app-created PO and GRN JSON records in deterministic checks."""
    if not isinstance(reference, dict):
        raise ValueError("Procurement reference data must be a dictionary.")

    merged = copy.deepcopy(reference)
    for key in ("purchase_orders", "goods_receipts"):
        if not isinstance(merged.get(key), list):
            merged[key] = []
        merged[key] = [row for row in merged[key] if isinstance(row, dict)]

    # A saved PO is the current record for its PO number; keep fixtures that
    # are not replaced by a saved record.
    for saved_po in load_saved_purchase_orders():
        wanted = _normalize_po(saved_po.get("po_number"))
        match_index = next(
            (
                index for index, row in enumerate(merged["purchase_orders"])
                if _normalize_po(row.get("po_number")) == wanted
            ),
            None,
        )
        if match_index is None:
            merged["purchase_orders"].append(saved_po)
        else:
            merged["purchase_orders"][match_index] = saved_po

    # Merge GRNs by GRN number so reference data can retain multiple partial
    # receipts for a PO while avoiding duplicate copies of the same GRN.
    for saved_grn in load_saved_goods_receipts():
        grn_number = _normalize_po(saved_grn.get("grn_number"))
        match_index = None
        if grn_number:
            match_index = next(
                (
                    index for index, row in enumerate(merged["goods_receipts"])
                    if _normalize_po(row.get("grn_number")) == grn_number
                ),
                None,
            )
        if match_index is None:
            merged["goods_receipts"].append(saved_grn)
        else:
            merged["goods_receipts"][match_index] = saved_grn

    return merged


def start_run(file_name):
    """Clear this invoice's per-run state and refresh procurement records."""
    global _reference_data_cache
    _reference_data_cache = None
    file_name = str(file_name)

    _review_reasons.pop(file_name, None)
    _dataset_results.pop(file_name, None)
    _run_plans.pop(file_name, None)

    for key in list(_tool_results):
        if key[0] == file_name:
            _tool_results.pop(key, None)


def review_reason(file_name):
    return _review_reasons.get(str(file_name))


def log_step(step, detail):
    """Write audit events without allowing logging failures to stop tools."""

    global _db_warning_shown

    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "step": step,
        "detail": detail,
    }

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    entry,
                    ensure_ascii=False,
                    default=str,
                ) + "\n"
            )

    except Exception as exc:
        print(
            "  [warning] Local audit log failed:",
            str(exc)[:120],
        )

    try:
        if not save_log(step, detail) and not _db_warning_shown:
            print(
                "  [warning] Could not write to the database. "
                "Using local logging only."
            )
            _db_warning_shown = True

    except Exception as exc:
        if not _db_warning_shown:
            print(
                "  [warning] Database logging failed:",
                str(exc)[:120],
            )
            _db_warning_shown = True

    print(
        "  [log] " + step + ": " +
        json.dumps(detail, ensure_ascii=False, default=str)[:160]
    )


def _load_invoice(file_name):
    invoice_dir = INVOICE_DIR.resolve()
    path = (invoice_dir / str(file_name)).resolve()

    if path.parent != invoice_dir or not path.is_file():
        return None

    try:
        data = get_invoice_data(path)

    except Exception as exc:
        log_step(
            "tool:load_invoice:error",
            {
                "file": file_name,
                "error": str(exc)[:150],
            },
        )
        return None

    if isinstance(data, list):
        data = data[0] if data else None

    return data


def verify_invoice(file_name):
    """Run independent checks in plain Python."""

    data = _load_invoice(file_name)

    if data is None:
        return ["Invoice could not be read."]

    problems = [
        issue["detail"]
        for issue in check_line_math(data)
    ]

    po_number = _invoice_po_reference(data)

    if not po_number:
        problems.append("No PO number on the invoice.")
        return problems

    po_result = find_po(po_number)

    if po_result.get("status") != "found":
        problems.append(po_result.get("note", "PO not found."))
        return problems

    po = po_result["po"]

    vendor_check = check_vendor(
        data.get("vendor") or data.get("vendor_name"),
        po.get("vendor") or po.get("vendor_name"),
        invoice_vendor_id=data.get("vendor_id"),
        po_vendor_id=po.get("vendor_id"),
    )

    if vendor_check["status"] != "match":
        problems.append(
            "Vendor mismatch: " + vendor_check["note"]
        )

    return problems


def read_invoice(file_name: str) -> dict:
    """Read an invoice. Do not cache failed reads."""

    file_name = str(file_name)

    key = _cache_key("read_invoice", file_name)
    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    try:
        data = _load_invoice(file_name)

    except Exception as exc:
        data = None
        log_step(
            "tool:read_invoice:error",
            {
                "file": file_name,
                "error": str(exc)[:150],
            },
        )

    if not isinstance(data, dict):
        result = {
            "error": (
                "Could not read the invoice '" +
                file_name + "'. Try reading it again."
            )
        }

        log_step(
            "tool:read_invoice",
            {
                "file": file_name,
                "result": result,
            },
        )

        # Important: failed results are not cached.
        return result

    log_step(
        "tool:read_invoice",
        {
            "file": file_name,
            "result": data,
        },
    )

    return _store_cached(key, data)


def check_invoice_math(file_name: str) -> dict:
    """Check line arithmetic and invoice totals."""

    file_name = str(file_name)

    key = _cache_key("check_invoice_math", file_name)
    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    try:
        data = _load_invoice(file_name)

    except Exception as exc:
        data = None
        error_text = str(exc)[:150]

    else:
        error_text = ""

    if data is None:
        result = {
            "math_ok": False,
            "issues": [],
            "error": error_text or "Invoice not available.",
        }

        log_step(
            "tool:check_invoice_math",
            {"file": file_name, "result": result},
        )

        return result

    issues = check_line_math(data)

    result = {
        "math_ok": len(issues) == 0,
        "issues": issues,
    }

    log_step(
        "tool:check_invoice_math",
        {"file": file_name, "result": result},
    )

    return _store_cached(key, result)


def lookup_po(file_name: str, po_number: str) -> dict:
    """
    Verify the supplied PO against the PO actually extracted
    from the specified invoice.
    """

    file_name = str(file_name)

    key = _cache_key(
        "lookup_po",
        file_name,
        po_number,
    )

    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    data = _load_invoice(file_name)

    if not isinstance(data, dict):
        return {
            "status": "cannot_check",
            "po": None,
            "note": "The invoice could not be read. PO lookup was not performed.",
        }

    actual_po = _invoice_po_reference(data)

    if not actual_po:
        return {
            "status": "not_found",
            "po": None,
            "note": "No PO reference was found in the invoice.",
        }

    supplied_po = str(po_number or "").strip()

    mismatch = (
        supplied_po
        and _normalize_po(supplied_po) != _normalize_po(actual_po)
    )

    if mismatch:
        log_step(
            "tool:lookup_po:input_mismatch",
            {
                "file": file_name,
                "supplied_po": supplied_po,
                "actual_invoice_po": actual_po,
            },
        )

    # Always search using the PO extracted from the original invoice.
    result = find_po(actual_po)

    result["invoice_po_reference"] = actual_po

    if mismatch:
        result["supplied_po_number"] = supplied_po
        result["note"] = (
            "The supplied PO did not match the invoice. "
            "Lookup used the actual invoice PO '" +
            actual_po + "'. " +
            result.get("note", "")
        )

    log_step(
        "tool:lookup_po",
        {
            "file": file_name,
            "result": result,
        },
    )

    return _store_cached(key, result)

def _find_department(department_id, reference_data):
    """Return a department record from reference data, or None."""
    target = _normalize_po(department_id)
    if not target:
        return None
    for dept in reference_data.get("departments", []):
        if _normalize_po(dept.get("department_id")) == target:
            return dept
    return None


def check_department_budget(file_name: str) -> dict:
    """
    Check whether the invoice's total fits within the remaining budget
    of the department that owns the matching purchase order.

    Returns a dict with:
        budget_ok: True | False | None (None = could not check)
        department_id, department_name
        budget_allocated, budget_consumed, budget_remaining
        invoice_total, projected_consumed
        shortfall
        note
    """

    file_name = str(file_name)

    key = _cache_key("check_department_budget", file_name)
    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    # 1. Load the invoice.
    data = _load_invoice(file_name)

    if not isinstance(data, dict):
        result = {
            "budget_ok": None,
            "note": "Invoice could not be read; budget check was not performed.",
        }
        log_step(
            "tool:check_department_budget",
            {"file": file_name, "result": result},
        )
        return result

    # 2. Get the invoice total.
    invoice_total = _to_float(
        _first_value(data, "total_amount", "total", "grand_total")
    )

    if invoice_total is None:
        result = {
            "budget_ok": None,
            "note": "Invoice total is missing or unreadable; budget check skipped.",
        }
        log_step(
            "tool:check_department_budget",
            {"file": file_name, "result": result},
        )
        return result

    # 3. Find the PO for this invoice.
    po_reference = _invoice_po_reference(data)

    if not po_reference:
        result = {
            "budget_ok": None,
            "invoice_total": invoice_total,
            "note": "Invoice has no PO reference; department cannot be determined.",
        }
        log_step(
            "tool:check_department_budget",
            {"file": file_name, "result": result},
        )
        return result

    po_result = find_po(po_reference)

    if po_result.get("status") != "found":
        result = {
            "budget_ok": None,
            "invoice_total": invoice_total,
            "po_reference": po_reference,
            "note": (
                "PO '" + po_reference + "' could not be found in reference data; "
                "department budget cannot be determined."
            ),
        }
        log_step(
            "tool:check_department_budget",
            {"file": file_name, "result": result},
        )
        return result

    po = po_result["po"]
    department_id = po.get("department_id")

    if not department_id:
        result = {
            "budget_ok": None,
            "invoice_total": invoice_total,
            "po_reference": po_reference,
            "note": "Purchase order has no department_id; budget check skipped.",
        }
        log_step(
            "tool:check_department_budget",
            {"file": file_name, "result": result},
        )
        return result

    # 4. Look up the department.
    reference_data = load_reference_data()
    department = _find_department(department_id, reference_data)

    if not department:
        result = {
            "budget_ok": None,
            "department_id": department_id,
            "invoice_total": invoice_total,
            "note": (
                "Department '" + str(department_id) + "' is not in the budget reference data."
            ),
        }
        log_step(
            "tool:check_department_budget",
            {"file": file_name, "result": result},
        )
        return result

    # 5. Compute the check.
    allocated = _to_float(department.get("budget_allocated"))
    consumed = _to_float(department.get("budget_consumed"))

    # Missing/invalid consumed amounts must not be treated as zero. Doing so
    # could incorrectly mark an invoice as within budget.
    if consumed is None:
        result = {
            "budget_ok": None,
            "department_id": department_id,
            "department_name": department.get("name"),
            "invoice_total": invoice_total,
            "note": "Department has no valid budget_consumed value; budget check could not be verified.",
        }
        log_step(
            "tool:check_department_budget",
            {"file": file_name, "result": result},
        )
        return _store_cached(key, result)


    if allocated is None:
        result = {
            "budget_ok": None,
            "department_id": department_id,
            "invoice_total": invoice_total,
            "note": "Department has no valid budget_allocated value.",
        }
        log_step(
            "tool:check_department_budget",
            {"file": file_name, "result": result},
        )
        return result

    remaining = allocated - consumed
    projected = consumed + invoice_total
    shortfall = max(0.0, projected - allocated)
    budget_ok = projected <= allocated + 0.01

    if budget_ok:
        note = (
            "Invoice of "
            + f"{invoice_total:,.2f}"
            + " fits within the remaining budget of "
            + f"{remaining:,.2f}"
            + " for "
            + str(department.get("name") or department_id)
            + "."
        )
    else:
        note = (
            "Invoice of "
            + f"{invoice_total:,.2f}"
            + " exceeds the remaining budget of "
            + f"{remaining:,.2f}"
            + " by "
            + f"{shortfall:,.2f}"
            + " for "
            + str(department.get("name") or department_id)
            + "."
        )

    result = {
        "budget_ok": budget_ok,
        "department_id": department_id,
        "department_name": department.get("name"),
        "budget_allocated": allocated,
        "budget_consumed": consumed,
        "budget_remaining": remaining,
        "invoice_total": invoice_total,
        "projected_consumed": projected,
        "shortfall": shortfall,
        "note": note,
    }

    log_step(
        "tool:check_department_budget",
        {"file": file_name, "result": result},
    )

    return _store_cached(key, result)

def check_vendor_matches_po(
    file_name: str,
    po_number: str
) -> dict:
    """Check vendor identity using the actual invoice PO."""

    file_name = str(file_name)

    key = _cache_key(
        "check_vendor_matches_po",
        file_name,
        po_number,
    )

    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    try:
        data = _load_invoice(file_name)

    except Exception as exc:
        return {
            "status": "cannot_check",
            "note": str(exc)[:150],
        }

    if not isinstance(data, dict):
        return {
            "status": "cannot_check",
            "note": "Invoice could not be read.",
        }

    actual_po = _invoice_po_reference(data)

    if not actual_po:
        return {
            "status": "cannot_check",
            "note": "No PO reference was found in the invoice.",
        }

    supplied_po = str(po_number or "").strip()

    if (
        supplied_po
        and _normalize_po(supplied_po) != _normalize_po(actual_po)
    ):
        log_step(
            "tool:check_vendor_matches_po:input_mismatch",
            {
                "file": file_name,
                "supplied_po": supplied_po,
                "actual_invoice_po": actual_po,
            },
        )

    po_result = find_po(actual_po)

    if po_result.get("status") != "found":
        return {
            "status": "cannot_check",
            "note": (
                "The invoice PO '" + actual_po +
                "' could not be found in saved or reference purchase orders."
            ),
        }

    po = po_result["po"]

    result = check_vendor(
        data.get("vendor") or data.get("vendor_name"),
        po.get("vendor") or po.get("vendor_name"),
        invoice_vendor_id=data.get("vendor_id"),
        po_vendor_id=po.get("vendor_id"),
    )

    log_step(
        "tool:check_vendor_matches_po",
        {
            "file": file_name,
            "actual_invoice_po": actual_po,
            "result": result,
        },
    )

    return _store_cached(key, result)


def _make_agent_history_record(data):
    """Convert extracted invoice data into the history shape."""

    if not isinstance(data, dict):
        return None

    document_type = str(
        data.get("document_type") or ""
    ).upper()

    if (
        "CREDIT_NOTE" in document_type
        or data.get("credit_note_number")
    ):
        return None

    invoice_number = _first_value(
        data,
        "invoice_number",
        "invoice_no",
        "invoice_id",
    )

    if not invoice_number:
        return None

    vendor = data.get("vendor")

    if isinstance(vendor, dict):
        vendor_name = _first_value(
            vendor,
            "name",
            "vendor_name",
        )
    else:
        vendor_name = vendor or data.get("vendor_name")

    items = data.get("items") or data.get("line_items") or []
    history_items = []

    for item in items:
        if not isinstance(item, dict):
            continue

        history_items.append({
            "description": _first_value(
                item,
                "description",
                "name",
                "product_name",
            ),
            "quantity_billed": _first_value(
                item,
                "quantity",
                "quantity_billed",
                "qty",
            ),
            "unit_price": _first_value(
                item,
                "unit_price",
                "unit_rate",
                "rate",
                "price",
            ),
        })

    return {
        "invoice_number": invoice_number,
        "vendor_name": vendor_name,
        "po_number": _invoice_po_reference(data),
        "status": "PROCESSED",
        "items": history_items,
    }


def check_dataset_rules(file_name: str) -> dict:
    """Run deterministic dataset checks once per invoice."""

    global _reference_data_cache

    file_name = str(file_name)

    key = _cache_key("check_dataset_rules", file_name)
    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    try:
        data = _load_invoice(file_name)

        if data is None:
            result = {
                "case_id": file_name,
                "recommendation": "NEEDS_HUMAN_REVIEW",
                "findings": ["INCOMPLETE_EXTRACTION"],
            }

        else:
            if _reference_data_cache is None:
                _reference_data_cache = _merge_saved_procurement_records(
                    load_reference_data()
                )

            history = [
                dict(row)
                for row in _reference_data_cache.get(
                    "prior_invoices", []
                )
                if "duplicate test case 07"
                not in str(row.get("note", "")).lower()
            ]

            history.extend(
                record
                for previous_file, record in _dataset_history.items()
                if previous_file != file_name
            )

            invoice_record = {
                "case_id": file_name,
                "document": data,
            }

            result = evaluate_invoice(
                invoice_record,
                reference_data=_reference_data_cache,
                history=history,
            )

            history_record = _make_agent_history_record(data)

            if history_record:
                _dataset_history[file_name] = history_record

    except Exception as exc:
        result = {
            "case_id": file_name,
            "recommendation": "NEEDS_HUMAN_REVIEW",
            "findings": ["INCOMPLETE_EXTRACTION"],
            "error": str(exc)[:150],
        }

    if not isinstance(result, dict):
        result = {
            "case_id": file_name,
            "recommendation": "NEEDS_HUMAN_REVIEW",
            "findings": ["INCOMPLETE_EXTRACTION"],
            "error": "Dataset rules returned an invalid result.",
        }

    _dataset_results[file_name] = copy.deepcopy(result)

    log_step(
        "tool:check_dataset_rules",
        {
            "file": file_name,
            "result": result,
        },
    )

    return _store_cached(key, result)


def get_dataset_result(file_name):
    """Return a copy of the deterministic result."""

    result = _dataset_results.get(str(file_name))

    return copy.deepcopy(result) if result is not None else None


def send_to_human_review(
    file_name: str,
    reason: str
) -> dict:
    """Record one human-review routing event per invoice per run."""

    file_name = str(file_name)

    if file_name in _review_reasons:
        log_step(
            "tool:send_to_human_review:duplicate_ignored",
            {"file": file_name},
        )

        return {
            "queued": True,
            "note": (
                "Already sent to human review. "
                "Nothing was approved or paid."
            ),
        }

    _review_reasons[file_name] = reason

    log_step(
        "tool:send_to_human_review",
        {
            "file": file_name,
            "reason": reason,
        },
    )

    return {
        "queued": True,
        "note": (
            "A human reviewer will take it from here. "
            "Nothing was approved or paid."
        ),
    }


# ==================================================
# AGENT PLANNING AND RECOVERY TOOLS
# ==================================================

_run_plans = {}


def record_plan(file_name: str, plan_steps: list[str], reasoning: str) -> dict:
    """
    Record your investigation plan for this invoice before running checks.
    Call it again with a revised plan whenever a tool result changes what
    you need to check (for example, the PO was not found).

    Args:
        file_name: The invoice file name being checked.
        plan_steps: Ordered list of the checks you intend to run next.
        reasoning: One or two sentences on why this plan fits this invoice.
    """

    file_name = str(file_name)
    steps = [
        str(step).strip()
        for step in (plan_steps or [])
        if str(step).strip()
    ][:12]
    reasoning = str(reasoning or "").strip()[:500]

    history = _run_plans.setdefault(file_name, [])
    revision = len(history) + 1
    history.append({"steps": steps, "reasoning": reasoning})

    log_step(
        "agent:plan" if revision == 1 else "agent:plan_revised",
        {
            "file": file_name,
            "revision": revision,
            "steps": steps,
            "reasoning": reasoning,
        },
    )

    return {
        "recorded": True,
        "revision": revision,
        "note": (
            "Plan recorded. Execute it. If a result changes what you need "
            "to check, call record_plan again with the revised plan."
        ),
    }


def _po_vendor(po):
    vendor = po.get("vendor_name") or po.get("vendor")
    if isinstance(vendor, dict):
        vendor = vendor.get("name") or vendor.get("vendor_name")
    return str(vendor or "").strip()


def _po_total(po):
    for key in ("total_amount", "grand_total", "total"):
        value = _to_float(po.get(key))
        if value is not None:
            return value

    total = 0.0
    found = False
    for item in po.get("items") or []:
        if not isinstance(item, dict):
            continue
        qty = _to_float(item.get("quantity_ordered") or item.get("quantity"))
        price = _to_float(item.get("approved_unit_price") or item.get("unit_price"))
        if qty is None or price is None:
            continue
        tax = _to_float(item.get("tax_percent")) or 0.0
        total += qty * price * (1 + tax / 100.0)
        found = True

    return round(total, 2) if found else None


def _item_words(items):
    words = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("description") or item.get("name") or "").lower()
        words.update(re.findall(r"[a-z0-9]{3,}", text))
    return words


def find_candidate_pos(file_name: str) -> dict:
    """
    Use this only when the invoice has no PO number, or lookup_po did not
    find the PO exactly. Searches purchase orders from the same vendor and
    ranks them by amount and item similarity. Results are suggestions for a
    human to confirm: never treat a candidate as the invoice's PO.

    Args:
        file_name: The invoice file name being checked.
    """

    file_name = str(file_name)
    key = _cache_key("find_candidate_pos", file_name)
    cached = _get_cached(key)
    if cached is not _CACHE_MISS:
        return cached

    data = _load_invoice(file_name)
    if not isinstance(data, dict):
        result = {
            "status": "cannot_check",
            "candidates": [],
            "note": "The invoice could not be read, so no PO candidates were searched.",
        }
        log_step("tool:find_candidate_pos", {"file": file_name, "result": result})
        return _store_cached(key, result)

    invoice_vendor = str(
        data.get("vendor") or data.get("vendor_name") or ""
    ).strip()
    invoice_vendor_id = str(data.get("vendor_id") or "").strip()
    invoice_total = _to_float(
        _first_value(data, "total_amount", "grand_total", "total", "invoice_total")
    )
    invoice_words = _item_words(data.get("line_items") or data.get("items"))
    printed_po = _invoice_po_reference(data)

    # Saved POs take precedence over static reference fixtures.
    pos = {}
    try:
        reference = load_reference_data()
        for po in reference.get("purchase_orders", []) or []:
            if isinstance(po, dict) and po.get("po_number"):
                pos[_normalize_po(po["po_number"])] = po
    except Exception as exc:
        log_step("tool:find_candidate_pos:reference_error", {"error": str(exc)[:150]})
    try:
        for po in load_saved_purchase_orders():
            pos[_normalize_po(po["po_number"])] = po
    except Exception as exc:
        log_step("tool:find_candidate_pos:saved_error", {"error": str(exc)[:150]})

    candidates = []
    for po in pos.values():
        if str(po.get("status") or "").upper() == "CANCELLED":
            continue

        po_vendor = _po_vendor(po)
        po_vendor_id = str(po.get("vendor_id") or "").strip()
        same_vendor = bool(
            (invoice_vendor_id and po_vendor_id and invoice_vendor_id == po_vendor_id)
            or (
                invoice_vendor and po_vendor
                and difflib.SequenceMatcher(
                    None, invoice_vendor.lower(), po_vendor.lower()
                ).ratio() >= 0.85
            )
        )
        if not same_vendor:
            continue

        po_total = _po_total(po)
        amount_gap_pct = None
        amount_score = 0.0
        if invoice_total and po_total:
            amount_gap_pct = abs(invoice_total - po_total) / po_total * 100
            amount_score = max(0.0, 1 - amount_gap_pct / 100)

        po_words = _item_words(po.get("items"))
        overlap = (
            len(invoice_words & po_words) / len(invoice_words)
            if invoice_words else 0.0
        )

        score = round(50 + 30 * amount_score + 20 * overlap, 1)

        why = ["same vendor"]
        if amount_gap_pct is not None:
            why.append(
                "amount matches" if amount_gap_pct < 0.5
                else "amount differs by " + str(round(amount_gap_pct, 1)) + "%"
            )
        if overlap:
            why.append("items overlap " + str(round(overlap * 100)) + "%")

        candidates.append({
            "po_number": po.get("po_number"),
            "vendor": po_vendor,
            "po_status": po.get("status"),
            "po_total": po_total,
            "match_score": score,
            "why": ", ".join(why),
        })

    candidates.sort(key=lambda c: (-c["match_score"], str(c["po_number"])))
    candidates = candidates[:3]

    result = {
        "status": "candidates_found" if candidates else "no_candidates",
        "printed_po": printed_po,
        "invoice_vendor": invoice_vendor,
        "invoice_total": invoice_total,
        "candidates": candidates,
        "note": (
            "Suggestions only. A human must confirm the correct PO with the "
            "vendor or procurement; the invoice stays in human review."
            if candidates else
            "No purchase orders from this vendor were found. Procurement "
            "should confirm whether a PO exists."
        ),
    }

    log_step("tool:find_candidate_pos", {"file": file_name, "result": result})
    return _store_cached(key, result)
