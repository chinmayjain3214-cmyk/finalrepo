"""Tests for budget_store: live department budgets and Finance changes."""

import json

import pytest

import budget_store as bs


REFERENCE = {
    "departments": [
        {"department_id": "DEPT-IT", "name": "Information Technology",
         "budget_allocated": 500000.0, "budget_consumed": 320000.0},
        {"department_id": "DEPT-EXAM", "name": "Examinations",
         "budget_allocated": 100000.0, "budget_consumed": 95000.0},
        {"department_id": "DEPT-ODD", "name": "No figures"},
    ],
    "purchase_orders": [],
}
FINANCE = {"username": "finance1", "role": "finance_officer"}
ADMIN = {"username": "admin1", "role": "admin"}


@pytest.fixture
def store(tmp_path):
    return tmp_path / "department_budgets.json"


def test_rows_use_dataset_figures(store):
    live = bs.apply_budget_changes(REFERENCE, store)
    row = bs.find_department_budget("DEPT-IT", live)
    assert row["allocated"] == 500000.0
    assert row["consumed"] == 320000.0
    assert row["available"] == 180000.0
    assert row["source"] == "DATASET"
    assert round(row["utilisation"], 1) == 64.0


def test_missing_figures_are_unknown_not_zero(store):
    row = bs.find_department_budget("DEPT-ODD", bs.apply_budget_changes(REFERENCE, store))
    assert row["available"] is None
    assert bs.budget_status(row) == "UNKNOWN"
    assert bs.budget_impact(row, 1000)["checked"] is False


def test_status_low_and_ok(store):
    live = bs.apply_budget_changes(REFERENCE, store)
    assert bs.budget_status(bs.find_department_budget("DEPT-EXAM", live)) == "LOW"
    assert bs.budget_status(bs.find_department_budget("DEPT-IT", live)) == "OK"


def test_impact_fits_and_exceeds(store):
    live = bs.apply_budget_changes(REFERENCE, store)
    exam = bs.find_department_budget("DEPT-EXAM", live)
    fits = bs.budget_impact(exam, 4000)
    assert fits["checked"] and not fits["exceeds"]
    assert fits["available_after"] == 1000.0
    over = bs.budget_impact(exam, 7500.5)
    assert over["exceeds"] is True
    assert over["shortfall"] == 2500.5
    exact = bs.budget_impact(exam, 5000)
    assert exact["exceeds"] is False


def test_finance_change_is_applied_and_recorded(store):
    entry = bs.set_department_budget(
        "DEPT-EXAM", "1,50,000", FINANCE, "Extra exam centre approved", REFERENCE, store
    )
    assert entry["previous_allocated"] == 100000.0
    assert entry["budget_allocated"] == 150000.0
    live = bs.apply_budget_changes(REFERENCE, store)
    row = bs.find_department_budget("DEPT-EXAM", live)
    assert row["allocated"] == 150000.0
    assert row["available"] == 55000.0
    assert row["source"] == "FINANCE_UPDATE"
    assert row["updated_by"] == "finance1"
    history = bs.budget_change_history(store)
    assert history[0]["reason"] == "Extra exam centre approved"
    # The dataset itself is never changed.
    assert REFERENCE["departments"][1]["budget_allocated"] == 100000.0


def test_latest_change_wins(store):
    bs.set_department_budget("DEPT-IT", 600000, ADMIN, "Mid-year top up", REFERENCE, store)
    live = bs.apply_budget_changes(REFERENCE, store)
    bs.set_department_budget("DEPT-IT", 550000, FINANCE, "Revised after audit", live, store)
    row = bs.find_department_budget("DEPT-IT", bs.apply_budget_changes(REFERENCE, store))
    assert row["allocated"] == 550000.0
    history = bs.budget_change_history(store)
    assert [h["budget_allocated"] for h in history] == [550000.0, 600000.0]
    assert history[0]["previous_allocated"] == 600000.0


@pytest.mark.parametrize("user", [
    None,
    {"username": "procurement1", "role": "procurement_officer"},
    {"username": "depthead1", "role": "department_head"},
    {"username": "", "role": "admin"},
])
def test_only_finance_and_admin_can_change(store, user):
    with pytest.raises(bs.BudgetError):
        bs.set_department_budget("DEPT-IT", 600000, user, "A valid reason", REFERENCE, store)
    assert not store.exists()


@pytest.mark.parametrize("amount", ["", "abc", "-5", "nan", "inf", "1e20"])
def test_bad_amounts_are_refused(store, amount):
    with pytest.raises(bs.BudgetError):
        bs.set_department_budget("DEPT-IT", amount, FINANCE, "A valid reason", REFERENCE, store)


def test_cannot_go_below_used_budget(store):
    with pytest.raises(bs.BudgetError, match="already used"):
        bs.set_department_budget("DEPT-IT", 300000, FINANCE, "Cut the budget", REFERENCE, store)


def test_reason_required_and_unknown_department(store):
    with pytest.raises(bs.BudgetError, match="reason"):
        bs.set_department_budget("DEPT-IT", 600000, FINANCE, "  ", REFERENCE, store)
    with pytest.raises(bs.BudgetError, match="not in the reference"):
        bs.set_department_budget("DEPT-NOPE", 600000, FINANCE, "A valid reason", REFERENCE, store)
    with pytest.raises(bs.BudgetError, match="already"):
        bs.set_department_budget("DEPT-IT", 500000, FINANCE, "A valid reason", REFERENCE, store)


def test_damaged_file_falls_back_to_dataset(store):
    store.write_text("{not json", encoding="utf-8")
    row = bs.find_department_budget("DEPT-IT", bs.apply_budget_changes(REFERENCE, store))
    assert row["allocated"] == 500000.0
    store.write_text(json.dumps({"budgets": {"DEPT-IT": {"budget_allocated": "-9"}}}), encoding="utf-8")
    row = bs.find_department_budget("DEPT-IT", bs.apply_budget_changes(REFERENCE, store))
    assert row["allocated"] == 500000.0


def test_clear_changes(store):
    bs.set_department_budget("DEPT-IT", 600000, FINANCE, "Mid-year top up", REFERENCE, store)
    assert bs.clear_budget_changes(store) is True
    assert bs.clear_budget_changes(store) is False
    row = bs.find_department_budget("DEPT-IT", bs.apply_budget_changes(REFERENCE, store))
    assert row["allocated"] == 500000.0


def test_agent_budget_check_uses_changed_budget(store, monkeypatch):
    """The agent's budget tool reads the live figures, not only the dataset."""
    import agent_tools

    bs.set_department_budget("DEPT-EXAM", 200000, FINANCE, "Approved increase", REFERENCE, store)
    monkeypatch.setattr(bs, "BUDGET_FILE", store)
    monkeypatch.setattr("rules.load_reference_data", lambda *a, **k: REFERENCE)
    live = agent_tools.load_reference_data()
    exam = [d for d in live["departments"] if d["department_id"] == "DEPT-EXAM"][0]
    assert exam["budget_allocated"] == 200000.0


def test_po_check_converts_foreign_currency(store):
    ref = dict(REFERENCE)
    ref["fx_rates"] = [{"currency": "USD", "base_currency": "INR", "reference_rate": 84.2}]
    live = bs.apply_budget_changes(ref, store)
    usd = bs.po_budget_check(live, "DEPT-EXAM", 100, "usd")
    assert usd["checked"] and usd["exchange_rate"] == 84.2
    assert usd["po_amount"] == 8420.0 and usd["exceeds"] is True
    inr = bs.po_budget_check(live, "DEPT-EXAM", 100, "INR")
    assert inr["po_amount"] == 100.0 and inr["exceeds"] is False
    eur = bs.po_budget_check(live, "DEPT-EXAM", 100, "EUR")
    assert eur["checked"] is False and "exchange rate" in eur["message"]
    none = bs.po_budget_check(live, "", 100, "INR")
    assert none["checked"] is False
