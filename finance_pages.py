"""Finance workspace for the invoice-agent demo.

Department budgets are live: they come from the reference dataset plus any
change a Finance Officer or Admin makes here (see budget_store.py), and the
agent's invoice budget check uses the same figures.

Department attribution is intentionally read from the purchase-order / requisition
record, never guessed from the vendor invoice. The payment schedule and the
unmapped-record examples below are still fictional preview data.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from budget_store import (
    EDIT_ROLES,
    BudgetError,
    budget_change_history,
    budget_status,
    department_budget_rows,
    set_department_budget,
)
from theme import amount_tile_html


# ---------------------------------------------------------------------------
# FICTIONAL PREVIEW DATA ONLY.
# No values below represent actual university budgets or payment obligations.
# ---------------------------------------------------------------------------

# This is an intentionally unmapped example to show the exception workflow.
# It is not a real invoice or PO and is excluded from all department totals.
DEMO_UNMAPPED_COMMITMENTS: list[dict[str, Any]] = [
    {
        "reference": "DEMO-PO-UNMAPPED-01",
        "vendor": "Example Vendor (fictional)",
        "amount": 8_500.00,
        "department": None,
        "reason": "The PO record has no department / cost-centre mapping.",
        "budget_status": "NOT VERIFIED",
    }
]

# Every scheduled/paid row below is a fictional, already human-approved example.
# Real records must only enter this list from the payment backend after approval.
DEMO_PAYMENTS: list[dict[str, Any]] = [
    {
        "invoice": "DEMO-AP-001",
        "vendor": "Example Lab Supplies",
        "po": "DEMO-PO-001",
        "department": "Academic Laboratories",
        "amount": 5_900.00,
        "invoice_date": "15 Nov 2026",
        "due_date": "18 Nov 2026",
        "terms": "Net 3",
        "status": "DUE SOON",
        "approval_status": "APPROVED",
    },
    {
        "invoice": "DEMO-AP-002",
        "vendor": "Example Electronics",
        "po": "DEMO-PO-002",
        "department": "Computer Science",
        "amount": 30_000.00,
        "invoice_date": "04 Nov 2026",
        "due_date": "10 Nov 2026",
        "terms": "Net 6",
        "status": "OVERDUE",
        "approval_status": "APPROVED",
    },
    {
        "invoice": "DEMO-AP-003",
        "vendor": "Example Campus Services",
        "po": "DEMO-PO-003",
        "department": "Campus Administration",
        "amount": 18_500.00,
        "invoice_date": "15 Nov 2026",
        "due_date": "15 Dec 2026",
        "terms": "Net 30",
        "status": "SCHEDULED",
        "approval_status": "APPROVED",
    },
    {
        "invoice": "DEMO-AP-004",
        "vendor": "Example Office Systems",
        "po": "DEMO-PO-004",
        "department": "Campus Administration",
        "amount": 12_750.00,
        "invoice_date": "01 Nov 2026",
        "due_date": "08 Nov 2026",
        "terms": "Net 7",
        "status": "PAID",
        "approval_status": "APPROVED",
    },
]

# An unmapped department example is deliberately NOT a payment-schedule row.
# It must be resolved and approved before it can be scheduled.
DEMO_BLOCKED_FROM_SCHEDULING: list[dict[str, Any]] = [
    {
        "invoice": "DEMO-UNASSIGNED-01",
        "vendor": "Example Vendor (fictional)",
        "po": "DEMO-PO-UNMAPPED-01",
        "amount": 8_500.00,
        "reason": "Department is missing from the PO master record.",
        "status": "NOT SCHEDULED",
    }
]


def _money(value: Any) -> str:
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _tile(column: Any, label: str, value: Any) -> None:
    """Metric tile that never hides the amount: hover shows the full value."""
    column.markdown(amount_tile_html(label, value), unsafe_allow_html=True)


STATUS_LABELS = {
    "OK": "Within budget",
    "LOW": "Low (10% or less left)",
    "EXCEEDED": "Over budget",
    "UNKNOWN": "No budget figures",
}


def _audit(step: str, detail: dict[str, Any]) -> None:
    try:
        from db_store import save_log

        save_log(step, detail)
    except Exception:
        pass


def _status_text(status: str) -> str:
    return str(status or "UNKNOWN").upper()


def _render_department_mapping_exceptions() -> None:
    st.markdown("### Department mapping exceptions")
    st.caption(
        "Unmapped records are kept separate from department budget totals. "
        "Their budget status is NOT VERIFIED until an authorized user resolves the mapping."
    )

    if not DEMO_UNMAPPED_COMMITMENTS:
        st.success("No unmapped department commitments in this preview.")
        return

    st.warning(
        f"{len(DEMO_UNMAPPED_COMMITMENTS)} illustrative record(s) have no department mapping. "
        "These amounts are excluded from the department totals above."
    )

    st.dataframe(
        [
            {
                "PO / reference": row["reference"],
                "Vendor": row["vendor"],
                "Department": "UNASSIGNED",
                "Amount": _money(row["amount"]),
                "Budget status": row["budget_status"],
                "Reason": row["reason"],
            }
            for row in DEMO_UNMAPPED_COMMITMENTS
        ],
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("How an unmapped department is handled"):
        st.markdown(
            "1. Read the PO number from the invoice.\n"
            "2. Look up that PO in the university procurement records.\n"
            "3. Read the department / cost centre from the PO or linked requisition.\n"
            "4. If the mapping is missing, show **UNASSIGNED** and **NOT VERIFIED**.\n"
            "5. Route the exception to Procurement / Finance for an authorized correction.\n\n"
            "The system must not infer a department from the vendor name, item description, "
            "or invoice text. The unmapped amount must not be silently charged to a default department."
        )


def _last_change_text(row: dict[str, Any]) -> str:
    if row.get("source") == "FINANCE_UPDATE":
        return f"{row.get('updated_by') or 'Finance'}, {str(row.get('updated_at') or '')[:10]}"
    return "Reference dataset"


def _render_budget_editor(user: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    st.markdown("### Change a department budget")
    role = str(user.get("role") or "").strip().lower()
    if role not in EDIT_ROLES:
        st.caption("Only a Finance Officer or Admin can change a department budget.")
        return

    st.caption(
        "Set a department's new allocated budget. A reason is required, and the "
        "budget cannot go below what the department has already used. The change "
        "applies straight away to new purchase orders and to the agent's invoice "
        "budget check, and it is written to the audit log."
    )
    if not rows:
        st.info("No departments with IDs were found in the reference data.")
        return

    labels = {f"{row['name']} ({row['department_id']})": row for row in rows}
    label = st.selectbox(
        "Department to change", list(labels), key="finance_budget_edit_department"
    )
    row = labels[label]
    current = row.get("allocated")
    st.caption(
        f"Allocated now: {_money(current)} · Already used: {_money(row.get('consumed'))}"
    )

    with st.form(f"finance_budget_edit_form_{row['department_id']}", clear_on_submit=False):
        new_amount = st.text_input(
            "New allocated budget (INR)",
            value="" if current is None else f"{current:.2f}",
            help="Commas are fine, for example 2,50,000.",
        )
        reason = st.text_area(
            "Reason for the change",
            placeholder="For example: mid-year top-up approved by the finance committee",
            max_chars=300,
        )
        submitted = st.form_submit_button("Save new budget", type="primary")

    if submitted:
        try:
            entry = set_department_budget(
                row["department_id"], new_amount, user, reason
            )
        except BudgetError as exc:
            st.error(str(exc))
        except OSError:
            st.error("The budget could not be saved. Check that the data folder is writable.")
        else:
            _audit("finance:budget_change", entry)
            st.session_state["finance_budget_notice"] = (
                f"{entry['department']}: allocated budget changed from "
                f"{_money(entry['previous_allocated'])} to {_money(entry['budget_allocated'])}."
            )
            st.rerun()


def _render_budget_history() -> None:
    history = budget_change_history()
    st.markdown("### Budget change history")
    if not history:
        st.caption("No budget changes yet. All budgets are the reference dataset figures.")
        return
    st.dataframe(
        [
            {
                "When (UTC)": str(row.get("updated_at") or "").replace("T", " ")[:16],
                "Department": row.get("department"),
                "From": _money(row.get("previous_allocated")),
                "To": _money(row.get("budget_allocated")),
                "Changed by": row.get("updated_by"),
                "Reason": row.get("reason"),
            }
            for row in history
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_budget_overview(user: dict[str, Any] | None = None) -> None:
    user = user or {}
    st.markdown("### Department budget overview")

    notice = st.session_state.pop("finance_budget_notice", None)
    if notice:
        st.success(notice)

    st.info(
        "Budgets come from the reference dataset (synthetic demo figures, not real "
        "university balances) plus any change saved here. New purchase orders and "
        "the agent's invoice budget check use these same figures."
    )
    st.markdown(
        "**Where does the department come from?** The vendor invoice does not need to "
        "contain a department. The system obtains the department / cost centre from the "
        "matched PO or linked purchase requisition."
    )

    rows = department_budget_rows()
    known = [row for row in rows if row.get("available") is not None]
    total_allocated = sum(row["allocated"] for row in known)
    total_used = sum(row["consumed"] for row in known)
    total_available = sum(row["available"] for row in known)
    needs_attention = sum(1 for row in rows if budget_status(row) in {"LOW", "EXCEEDED"})
    unassigned_count = len(DEMO_UNMAPPED_COMMITMENTS)

    metric_cols = st.columns(5)
    _tile(metric_cols[0], "Total allocated", _money(total_allocated))
    _tile(metric_cols[1], "Already used", _money(total_used))
    _tile(metric_cols[2], "Available", _money(total_available))
    _tile(metric_cols[3], "Low or over budget", needs_attention)
    _tile(metric_cols[4], "Unassigned records", unassigned_count)

    st.caption(
        "Available = allocated − already used, the same rule the invoice budget check "
        "uses. Unassigned records are excluded from these totals until their PO / "
        "requisition mapping is resolved."
    )
    st.divider()

    departments = ["All departments"] + [row["name"] for row in rows]
    selected = st.selectbox(
        "Filter by department",
        departments,
        key="finance_budget_department_filter",
    )
    visible = rows if selected == "All departments" else [
        row for row in rows if row["name"] == selected
    ]

    st.dataframe(
        [
            {
                "Department": row["name"],
                "Department ID": row["department_id"],
                "Allocated": _money(row["allocated"]),
                "Already used": _money(row["consumed"]),
                "Available": _money(row["available"]),
                "Used": (
                    f"{row['utilisation']:.1f}%" if row.get("utilisation") is not None else "—"
                ),
                "Status": STATUS_LABELS[budget_status(row)],
                "Allocated budget set by": _last_change_text(row),
            }
            for row in visible
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### Budget utilization")
    for row in visible:
        if row.get("utilisation") is None:
            st.markdown(f"**{row['name']}** · no budget figures")
            continue
        st.markdown(f"**{row['name']}** · {row['utilisation']:.1f}% used")
        st.progress(min(max(row["utilisation"] / 100.0, 0.0), 1.0))
        status = budget_status(row)
        if status == "EXCEEDED":
            st.error("Budget exceeded")
        elif status == "LOW":
            st.warning("Remaining balance is low")

    st.divider()
    _render_budget_editor(user, rows)
    st.divider()
    _render_budget_history()
    st.divider()
    _render_department_mapping_exceptions()


def _render_blocked_scheduling_examples() -> None:
    st.markdown("### Not eligible for scheduling")
    st.caption(
        "These illustrative records are intentionally excluded from the payment schedule. "
        "An invoice must be human-approved and have its required procurement / budget checks resolved first."
    )

    if not DEMO_BLOCKED_FROM_SCHEDULING:
        st.success("No blocked examples in this preview.")
        return

    st.warning(
        "A missing department mapping means the budget cannot be verified. "
        "The record stays out of the payment schedule until the mapping is resolved and approval is complete."
    )
    st.dataframe(
        [
            {
                "Invoice": row["invoice"],
                "Vendor": row["vendor"],
                "PO": row["po"],
                "Department": "UNASSIGNED",
                "Amount": _money(row["amount"]),
                "Payment status": row["status"],
                "Reason": row["reason"],
            }
            for row in DEMO_BLOCKED_FROM_SCHEDULING
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_payment_schedule() -> None:
    st.markdown("### Payment schedule")
    st.info(
        "Preview mode — all payment rows are fictional examples of invoices that have "
        "already received human approval. No payment has been scheduled, sent, or marked as paid by this screen."
    )

    rows = [row for row in DEMO_PAYMENTS if row.get("approval_status") == "APPROVED"]
    statuses = ("SCHEDULED", "DUE SOON", "OVERDUE", "PAID")
    status_counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in statuses
    }
    unpaid_total = sum(float(row["amount"]) for row in rows if row["status"] != "PAID")

    metrics = st.columns(5)
    _tile(metrics[0], "Scheduled", status_counts["SCHEDULED"])
    _tile(metrics[1], "Due soon", status_counts["DUE SOON"])
    _tile(metrics[2], "Overdue", status_counts["OVERDUE"])
    _tile(metrics[3], "Paid", status_counts["PAID"])
    _tile(metrics[4], "Unpaid total", _money(unpaid_total))

    st.divider()
    selected_status = st.selectbox(
        "Filter payment status",
        ["All statuses", *statuses],
        key="finance_payment_status_filter",
    )
    search = st.text_input(
        "Search payments",
        placeholder="Invoice number, vendor, PO, or department...",
        key="finance_payment_search",
    ).strip().casefold()

    visible = []
    for row in rows:
        if selected_status != "All statuses" and row["status"] != selected_status:
            continue
        searchable = " ".join(
            str(row.get(key, ""))
            for key in ("invoice", "vendor", "po", "department")
        ).casefold()
        if search and search not in searchable:
            continue
        visible.append(row)

    st.caption(f"Showing {len(visible)} of {len(rows)} illustrative approved payment records.")
    if visible:
        st.dataframe(
            [
                {
                    "Invoice": row["invoice"],
                    "Vendor": row["vendor"],
                    "PO": row["po"],
                    "Department (from PO)": row["department"] or "UNASSIGNED",
                    "Amount": _money(row["amount"]),
                    "Invoice date": row["invoice_date"],
                    "Due date": row["due_date"],
                    "Terms": row["terms"],
                    "Status": _status_text(row["status"]),
                }
                for row in visible
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No illustrative approved payments match the selected filters.")

    st.divider()
    _render_blocked_scheduling_examples()

    st.divider()
    st.markdown("### Payment record actions")
    st.caption(
        "Actions will be enabled after the payment API and audit trail are connected. "
        "A payment can only be marked paid after Finance confirms settlement."
    )
    st.button(
        "Mark as paid (backend connection required)",
        disabled=True,
        help="Disabled in preview mode so this screen cannot change payment status.",
    )


def render_finance_page(user: dict[str, Any]) -> None:
    """Render the finance workspace for Finance Officers and Admins."""
    role = str(user.get("role") or "").strip().lower()
    if role not in {"admin", "finance_officer"}:
        st.error("You do not have permission to open the finance workspace.")
        return

    st.markdown(
        "<div class='page-eyebrow'>FINANCE OPERATIONS</div>",
        unsafe_allow_html=True,
    )
    st.title("Finance workspace")
    st.caption(
        "Review department budget utilization, resolve missing department mappings, "
        "and track approved invoice payment timelines."
    )

    budget_tab, payment_tab = st.tabs(["Budget overview", "Payment schedule"])
    with budget_tab:
        render_budget_overview(user)
    with payment_tab:
        render_payment_schedule()
