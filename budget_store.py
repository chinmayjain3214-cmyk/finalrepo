"""Department budgets: the dataset figures plus changes made by Finance.

The reference dataset (dataset/reference_data.json) holds each department's
allocated and used budget. Finance Officers and Admins can change a
department's allocated budget from the finance workspace. Those changes are
kept in data/department_budgets.json, never written into the dataset, so
the original figures stay available and every change has a reason, a person
and a time.

Everything that checks an invoice against a budget reads the figures through
load_live_reference_data(), so the PO page, the finance workspace and the
agent's budget check always use the same numbers.

Used budget (budget_consumed) is not editable here: it is spending already
recorded against the department.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
BUDGET_FILE = BASE_DIR / "data" / "department_budgets.json"

# Roles allowed to change a department's allocated budget.
EDIT_ROLES = {"finance_officer", "admin"}

MAX_BUDGET = Decimal("1000000000000")  # 1 lakh crore: far above any department
MIN_REASON_LENGTH = 5
MAX_REASON_LENGTH = 300
MAX_HISTORY = 200

# When this share of the budget or less is left, the department is "low".
LOW_BALANCE_SHARE = 0.10


class BudgetError(ValueError):
    """A budget change that cannot be accepted, with a message for the user."""


# ==================================================
# READING
# ==================================================

def _empty_store() -> dict[str, Any]:
    return {"budgets": {}, "history": []}


def load_budget_changes(path: str | Path | None = None) -> dict[str, Any]:
    """Return the saved budget changes, or an empty store.

    A missing or damaged file never stops the app: the dataset figures are
    used instead.
    """
    file_path = Path(path) if path else BUDGET_FILE
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return _empty_store()

    if not isinstance(data, dict):
        return _empty_store()
    budgets = data.get("budgets")
    history = data.get("history")
    return {
        "budgets": budgets if isinstance(budgets, dict) else {},
        "history": history if isinstance(history, list) else [],
    }


def _valid_amount(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def apply_budget_changes(
    reference: dict[str, Any],
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a copy of the reference data with Finance's budget changes applied."""
    if not isinstance(reference, dict):
        return reference

    changes = load_budget_changes(path).get("budgets", {})
    result = dict(reference)
    departments = reference.get("departments")
    if not isinstance(departments, list):
        return result

    updated = []
    for department in departments:
        if not isinstance(department, dict):
            updated.append(department)
            continue
        row = copy.deepcopy(department)
        change = changes.get(str(row.get("department_id") or ""))
        amount = _valid_amount(change.get("budget_allocated")) if isinstance(change, dict) else None
        if amount is not None:
            row["budget_allocated"] = amount
            row["budget_source"] = "FINANCE_UPDATE"
            row["budget_updated_by"] = change.get("updated_by")
            row["budget_updated_at"] = change.get("updated_at")
        else:
            row["budget_source"] = "DATASET"
        updated.append(row)

    result["departments"] = updated
    return result


def load_live_reference_data(path: str | Path | None = None) -> dict[str, Any]:
    """The reference dataset with the current department budgets."""
    from rules import load_reference_data

    return apply_budget_changes(load_reference_data(), path)


# ==================================================
# BUDGET FIGURES
# ==================================================

def budget_row(department: dict[str, Any]) -> dict[str, Any]:
    """Allocated, used and available budget for one department record."""
    allocated = _valid_amount(department.get("budget_allocated"))
    consumed = _valid_amount(department.get("budget_consumed"))
    available = None
    utilisation = None
    if allocated is not None and consumed is not None:
        available = allocated - consumed
        utilisation = (consumed / allocated * 100) if allocated > 0 else None

    return {
        "department_id": str(department.get("department_id") or ""),
        "name": str(department.get("name") or ""),
        "allocated": allocated,
        "consumed": consumed,
        "available": available,
        "utilisation": utilisation,
        "source": department.get("budget_source") or "DATASET",
        "updated_by": department.get("budget_updated_by"),
        "updated_at": department.get("budget_updated_at"),
    }


def department_budget_rows(reference: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Budget figures for every department, sorted by name."""
    if reference is None:
        reference = load_live_reference_data()
    departments = reference.get("departments", []) if isinstance(reference, dict) else []
    rows = [
        budget_row(row)
        for row in departments
        if isinstance(row, dict) and row.get("department_id")
    ]
    return sorted(rows, key=lambda row: (row["name"].casefold(), row["department_id"]))


def find_department_budget(
    department_id: Any,
    reference: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    wanted = str(department_id or "").strip()
    if not wanted:
        return None
    for row in department_budget_rows(reference):
        if row["department_id"] == wanted:
            return row
    return None


def budget_status(row: dict[str, Any] | None) -> str:
    """OK, LOW, EXCEEDED or UNKNOWN for a department's current balance."""
    if not row or row.get("available") is None or row.get("allocated") is None:
        return "UNKNOWN"
    if row["available"] < 0:
        return "EXCEEDED"
    if row["available"] <= row["allocated"] * LOW_BALANCE_SHARE:
        return "LOW"
    return "OK"


def budget_impact(row: dict[str, Any] | None, amount: Any) -> dict[str, Any]:
    """What a new purchase order does to the department's available budget.

    Uses the same rule as the invoice budget check: available = allocated - used.
    """
    po_amount = _valid_amount(amount)
    if not row or row.get("available") is None or po_amount is None:
        return {
            "checked": False,
            "exceeds": None,
            "available_before": None,
            "po_amount": po_amount,
            "available_after": None,
            "shortfall": None,
            "message": "The department budget could not be checked.",
        }

    before = float(row["available"])
    after = before - po_amount
    exceeds = after < -0.005
    if exceeds:
        message = (
            "This purchase order is more than the department's available budget. "
            "It can still be saved, but unless Finance raises the budget, invoices "
            "against it will be flagged BUDGET_EXCEEDED for a human to decide."
        )
    else:
        message = "This purchase order fits within the department's available budget."

    return {
        "checked": True,
        "exceeds": exceeds,
        "available_before": round(before, 2),
        "po_amount": round(po_amount, 2),
        "available_after": round(after, 2),
        "shortfall": round(-after, 2) if exceeds else 0.0,
        "message": message,
    }


BUDGET_CURRENCY = "INR"


def exchange_rate(currency: Any, reference: dict[str, Any] | None) -> float | None:
    """Reference rate to convert one unit of currency into INR, if known."""
    code = str(currency or BUDGET_CURRENCY).strip().upper()
    if code == BUDGET_CURRENCY:
        return 1.0
    rates = reference.get("fx_rates", []) if isinstance(reference, dict) else []
    for row in rates if isinstance(rates, list) else []:
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("currency") or "").upper() == code
            and str(row.get("base_currency") or BUDGET_CURRENCY).upper() == BUDGET_CURRENCY
        ):
            rate = _valid_amount(row.get("reference_rate"))
            if rate:
                return rate
    return None


def po_budget_check(
    reference: dict[str, Any] | None,
    department_id: Any,
    amount: Any,
    currency: Any = BUDGET_CURRENCY,
) -> dict[str, Any]:
    """Budget impact of a purchase order, converted to INR when needed.

    Department budgets are in INR. A PO in another currency is converted with
    the reference exchange rate; without a rate the budget is not checked.
    """
    row = find_department_budget(department_id, reference)
    code = str(currency or BUDGET_CURRENCY).strip().upper()
    rate = exchange_rate(code, reference)
    po_amount = _valid_amount(amount)

    if rate is None:
        result = budget_impact(None, amount)
        result["message"] = (
            f"Department budgets are in INR and there is no reference exchange rate "
            f"for {code}, so the budget was not checked."
        )
    else:
        converted = po_amount * rate if po_amount is not None else None
        result = budget_impact(row, converted)

    result.update({
        "department_id": str(department_id or "") or None,
        "currency": code,
        "exchange_rate": rate,
        "po_amount_in_po_currency": po_amount,
        "budget_currency": BUDGET_CURRENCY,
        "budget_source": (row or {}).get("source"),
    })
    return result


# ==================================================
# CHANGING A BUDGET
# ==================================================

def parse_budget_amount(value: Any) -> Decimal:
    """Read a budget amount typed by a person (commas and ₹ are allowed)."""
    text = str(value if value is not None else "").strip()
    text = text.replace(",", "").replace("₹", "").replace("INR", "").strip()
    if not text:
        raise BudgetError("Enter the new allocated budget.")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise BudgetError("The budget must be a number, for example 250000.") from None
    if not amount.is_finite():
        raise BudgetError("The budget must be a number, for example 250000.")
    if amount < 0:
        raise BudgetError("The budget cannot be negative.")
    if amount > MAX_BUDGET:
        raise BudgetError("That budget is too large. Check the number of zeros.")
    return amount.quantize(Decimal("0.01"))


def _write_atomically(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=".department_budgets_", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def set_department_budget(
    department_id: Any,
    new_allocated: Any,
    user: dict[str, Any] | None,
    reason: Any,
    reference: dict[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Change a department's allocated budget and return the history entry.

    Only Finance Officers and Admins may do this, a reason is required, and
    the budget cannot be set below what the department has already used.
    """
    role = str((user or {}).get("role") or "").strip().lower()
    username = str((user or {}).get("username") or "").strip()
    if role not in EDIT_ROLES or not username:
        raise BudgetError("Only a Finance Officer or Admin can change a department budget.")

    reason_text = " ".join(str(reason or "").split())
    if len(reason_text) < MIN_REASON_LENGTH:
        raise BudgetError("Give a short reason for the change, for example the approval it is based on.")
    if len(reason_text) > MAX_REASON_LENGTH:
        raise BudgetError(f"Keep the reason under {MAX_REASON_LENGTH} characters.")

    amount = parse_budget_amount(new_allocated)

    if reference is None:
        reference = load_live_reference_data(path)
    current = find_department_budget(department_id, reference)
    if current is None:
        raise BudgetError("That department is not in the reference data.")

    consumed = current.get("consumed")
    if consumed is not None and float(amount) < consumed - 0.005:
        raise BudgetError(
            "The budget cannot be lower than what the department has already used "
            f"(₹{consumed:,.2f})."
        )

    previous = current.get("allocated")
    if previous is not None and abs(float(amount) - previous) < 0.005:
        raise BudgetError("That is already the department's allocated budget.")

    file_path = Path(path) if path else BUDGET_FILE
    store = load_budget_changes(file_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {
        "department_id": current["department_id"],
        "department": current["name"],
        "previous_allocated": previous,
        "budget_allocated": float(amount),
        "reason": reason_text,
        "updated_by": username,
        "role": role,
        "updated_at": now,
    }
    store["budgets"][current["department_id"]] = {
        "budget_allocated": float(amount),
        "reason": reason_text,
        "updated_by": username,
        "updated_at": now,
    }
    store["history"] = (store["history"] + [entry])[-MAX_HISTORY:]
    _write_atomically(file_path, store)
    return entry


def budget_change_history(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Budget changes, newest first."""
    history = load_budget_changes(path).get("history", [])
    return [row for row in reversed(history) if isinstance(row, dict)]


def clear_budget_changes(path: str | Path | None = None) -> bool:
    """Go back to the dataset budgets. Returns True if a file was removed."""
    file_path = Path(path) if path else BUDGET_FILE
    try:
        file_path.unlink()
        return True
    except FileNotFoundError:
        return False
