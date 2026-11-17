"""One-time clean-up before submission.

Moves files the project no longer needs (backups, scratch scripts, junk data,
logs and test uploads) into _removed_before_submission/, keeping their folder
layout. Nothing is deleted: check that folder, then delete it yourself.
Finally the script moves itself and its .bat file there too.
"""

from __future__ import annotations

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "_removed_before_submission"

# Exact files (relative paths).
FILES = [
    "New Text Document.txt",
    "test.py",
    "json_test.py",
    "ingest_api.py",
    "sample_data.py",
    "scanner.txt",
    "setup.log",
    "agent_log.jsonl",
    "test_approval_evidence.py",  # an old copy of approval_evidence.py, not tests
    "docs/PS09.pptx",
    "docs/~$PS09.pptx",
    "data/goods_receipts/103.json",
    "data/goods_receipts/good receipt 102.json",
    "data/goods_receipts/p.txt",
    "data/goods_receipts/grn_135.json",
    "data/invoices/104.json",
    "data/invoices/this is bill of 103.json",
    "data/invoices/inv_131.json",
    "data/purchase_orders/104.json",
    "data/purchase_orders/n.txt",
    "data/purchase_orders/purchase order 103.json",
    "data/purchase_orders/this is po of 102.json",
    "data/purchase_orders/po_140.json",
    "data/purchase_orders/po_141.json",
    "data/purchase_orders/po_142.json",
    "data/purchase_orders/po_143.json",
    "data/department_budgets.json",
]

# Glob patterns (relative to the project folder).
PATTERNS = [
    "*.bak",
    "*.bak[0-9]*",
    ".streamlit/*.bak*",
    "invoices/upload_*",
    "data/invoices/upload_*",
    "data/audit_reports/*.json",
    "data/recovery/approval_requests/*.json",
]

# Whole folders.
FOLDERS = [
    "extracted",
]


def move(path: Path) -> bool:
    if not path.exists():
        return False
    relative = path.relative_to(HERE)
    destination = TARGET / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination = destination.with_name(destination.name + ".dup")
    try:
        shutil.move(str(path), str(destination))
    except OSError as exc:
        print(f"[SKIP] {relative} ({exc})")
        return False
    print(f"[MOVED] {relative}")
    return True


def main() -> int:
    TARGET.mkdir(exist_ok=True)
    count = 0
    for name in FILES:
        count += move(HERE / name)
    for pattern in PATTERNS:
        for path in sorted(HERE.glob(pattern)):
            if path.is_file():
                count += move(path)
    for name in FOLDERS:
        count += move(HERE / name)
    for cache in sorted(HERE.rglob("__pycache__")):
        if ".venv" in cache.parts or TARGET in cache.parents:
            continue
        shutil.rmtree(cache, ignore_errors=True)

    # Move this clean-up tool too; it is not part of the submission.
    for name in ("cleanup_for_submission.bat", "cleanup_for_submission.py"):
        count += move(HERE / name)

    print()
    print(f"Done. {count} items moved to {TARGET.name}\\")
    print("Check that folder, then delete it. It is excluded from git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
