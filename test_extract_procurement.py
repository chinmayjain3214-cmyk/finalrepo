"""Tests for reading PO / GRN documents: everything around the AI's reading.

No Gemini call and no database: these cover turning Word, Excel and JSON
files into text, and the Python that checks, matches and caps what the model
returned before it reaches the form.
"""

import io
import json
import sys
import types
import zipfile
from datetime import date

import pytest

# Streamlit is only needed when rendering UI; these tests use pure helpers.
sys.modules.setdefault("streamlit", types.ModuleType("streamlit"))

import extract_procurement as ep
from grn_pages import grn_form_values
from po_pages import MAX_PO_ITEMS, po_form_values


VENDORS = ["Acme Traders", "Digital World Electronics", "Nova Computer Systems"]
DEPARTMENTS = [
    {"department_id": "DEPT-IT", "name": "Information Technology", "cost_center": None},
    {"department_id": "DEPT-LIB", "name": "Library", "cost_center": None},
]


def _po(**overrides):
    raw = {
        "document_type": "Purchase Order",
        "po_number": "PO-140",
        "vendor_name": "Digital World Electronics",
        "buyer_department": "Department of Information Technology",
        "currency": "inr",
        "items": [
            {"description": "Wireless Mouse", "quantity": "10", "unit_price": "650.00", "tax_percent": 18},
            {"description": "USB-C Dock", "quantity": 4, "unit_price": "4,200", "tax_percent": 18},
        ],
        "subtotal": "23,300.00",
        "total": 27494,
    }
    raw.update(overrides)
    return ep.normalise_po(raw)


def _plan(extracted, used=(101, 130)):
    return ep.plan_po_prefill(extracted, VENDORS, DEPARTMENTS, ["INR", "USD"], used)


# ---------- small helpers ----------

@pytest.mark.parametrize("value, expected", [
    ("1,200.50", 1200.5), ("₹ 1200", 1200.0), (5, 5.0), ("", None),
    (None, None), ("abc", None), (True, None), (float("nan"), None),
])
def test_to_number(value, expected):
    assert ep.to_number(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("2026-11-15", date(2026, 11, 15)),
    ("15/11/2026", date(2026, 11, 15)),
    ("15 Nov 2026", date(2026, 11, 15)),
    ("15th November, 2026", date(2026, 11, 15)),
    ("sometime last week", None),
    ("", None),
])
def test_parse_date(value, expected):
    assert ep.parse_date(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("PO-140", 140), ("po 140", 140), ("140", 140),
    ("JKLU/PO/2026/140", None), ("PO-0", None), ("", None),
])
def test_po_number_to_id(value, expected):
    assert ep.po_number_to_id(value) == expected


# ---------- normalising the model's JSON ----------

def test_normalise_po_cleans_numbers_and_names():
    po = _po()
    assert po["document_type"] == "purchase_order"
    assert po["currency"] == "INR"
    assert po["subtotal"] == 23300.0
    assert po["items"][1] == {
        "description": "USB-C Dock", "quantity": 4.0, "unit": "",
        "unit_price": 4200.0, "tax_percent": 18.0, "line_total": None,
    }


def test_normalise_rejects_non_objects_and_skips_empty_lines():
    assert ep.normalise_po("not json") is None
    assert ep.normalise_grn(None) is None
    po = ep.normalise_po({"items": [{}, "junk", {"description": "Pen"}]})
    assert [item["description"] for item in po["items"]] == ["Pen"]


def test_long_or_multiline_text_is_bounded():
    po = ep.normalise_po({"vendor_name": "A\n" + "x" * 500})
    assert "\n" not in po["vendor_name"]
    assert len(po["vendor_name"]) <= ep.MAX_TEXT_LENGTH


# ---------- matching to reference data ----------

def test_vendor_matches_despite_case_and_punctuation():
    assert ep.match_option("digital world electronics.", VENDORS) == "Digital World Electronics"
    assert ep.match_option("Digital World Electronic", VENDORS) == "Digital World Electronics"


def test_unknown_vendor_is_never_guessed():
    assert ep.match_option("Shady Imports LLP", VENDORS) is None
    assert ep.match_option("", VENDORS) is None


def test_department_matches_by_id_or_name():
    assert ep.match_department("DEPT-LIB", DEPARTMENTS)["name"] == "Library"
    assert ep.match_department("Dept. of Information Technology", DEPARTMENTS)["department_id"] == "DEPT-IT"
    assert ep.match_department("Sports", DEPARTMENTS) is None


# ---------- purchase order plan ----------

def test_clean_po_fills_everything():
    plan = _plan(_po())
    assert plan["vendor"] == "Digital World Electronics"
    assert plan["department"]["department_id"] == "DEPT-IT"
    assert plan["currency"] == "INR"
    assert plan["printed_po_id"] == 140
    assert plan["items"] == [
        {"description": "Wireless Mouse", "quantity": "10", "unit_price": "650"},
        {"description": "USB-C Dock", "quantity": "4", "unit_price": "4200"},
    ]
    assert plan["warnings"] == []
    assert any("add up" in note for note in plan["notes"])


def test_unknown_vendor_and_department_are_reported_not_filled():
    plan = _plan(_po(vendor_name="Shady Imports LLP", buyer_department="Sports"))
    assert plan["vendor"] is None and plan["department"] is None
    text = " ".join(plan["warnings"])
    assert "Shady Imports LLP" in text and "department" in text.lower()


def test_subtotal_mismatch_is_flagged():
    plan = _plan(_po(subtotal=99999))
    assert any("subtotal" in warning for warning in plan["warnings"])


def test_existing_po_number_is_not_reused():
    plan = _plan(_po(po_number="PO-130"))
    assert plan["printed_po_id"] is None
    assert any("already exists" in warning for warning in plan["warnings"])


def test_odd_po_number_format_falls_back_to_assigned_number():
    plan = _plan(_po(po_number="JKLU/PO/2026/140"))
    assert plan["printed_po_id"] is None
    assert any("PO-123 format" in warning for warning in plan["warnings"])


def test_wrong_document_type_is_flagged():
    plan = _plan(_po(document_type="Tax Invoice"))
    assert any("not a purchase order" in warning for warning in plan["warnings"])


def test_unreadable_or_non_positive_values_are_left_blank():
    plan = _plan(_po(items=[{"description": "Chair", "quantity": -3, "unit_price": None}]))
    assert plan["items"] == [{"description": "Chair", "quantity": "", "unit_price": ""}]
    assert any("Line 1" in warning for warning in plan["warnings"])


def test_more_lines_than_the_form_holds():
    items = [{"description": f"Item {n}", "quantity": 1, "unit_price": 10} for n in range(12)]
    plan = _plan(_po(items=items, subtotal=None))
    assert len(plan["items"]) == MAX_PO_ITEMS
    assert any("first 8" in warning for warning in plan["warnings"])


def test_po_form_values_only_use_matched_reference_values():
    extracted = _po(vendor_name="Shady Imports LLP")
    values = po_form_values(_plan(extracted), extracted, has_vendor_list=True)
    assert "po_vendor_select" not in values
    assert values["po_item_count"] == 2
    assert values["po_item_description_0"] == "Wireless Mouse"
    assert values["po_item_price_text_1"] == "4200"
    assert values["po_item_description_7"] == ""
    assert values["po_department"] == "Information Technology (DEPT-IT)"
    assert values["po_currency_select"] == "INR"


# ---------- goods receipt plan ----------

LINES = [
    {"description": "Laptop", "remaining_to_receive": 5.0},
    {"description": "Laptop Bag", "remaining_to_receive": 2.0},
]


def _grn(**overrides):
    raw = {
        "document_type": "Goods Receipt Note",
        "grn_number": "GRN-7781",
        "po_number": "po 135",
        "vendor_name": "Nova Computer Systems",
        "received_date": "16/11/2026",
        "delivery_reference": "DC-4471",
        "items": [
            {"description": "Laptop", "quantity_received": 5, "quantity_accepted": 4, "quantity_rejected": 1},
            {"description": "Laptop bag", "quantity_received": 2, "quantity_accepted": 2},
        ],
    }
    raw.update(overrides)
    return ep.normalise_grn(raw)


def test_po_number_on_receipt_must_be_an_open_po():
    assert ep.match_po_number("po 135", ["PO-130", "PO-135"]) == "PO-135"
    assert ep.match_po_number("PO-9999", ["PO-130", "PO-135"]) is None
    assert ep.match_po_number("", ["PO-130"]) is None


def test_clean_receipt_maps_onto_po_lines():
    plan = ep.plan_grn_prefill(_grn(), LINES)
    assert plan["lines"] == [
        {"received": 5.0, "accepted": 4.0, "rejected": 1.0},
        {"received": 2.0, "accepted": 2.0, "rejected": 0.0},
    ]
    assert plan["received_date"] == date(2026, 11, 16)
    assert plan["delivery_reference"] == "DC-4471"


def test_over_delivery_is_capped_and_reported():
    grn = _grn(items=[{"description": "Laptop", "quantity_received": 9, "quantity_accepted": 9}])
    plan = ep.plan_grn_prefill(grn, LINES)
    assert plan["lines"][0] == {"received": 5.0, "accepted": 5.0, "rejected": 0.0}
    assert any("only 5 is still outstanding" in warning for warning in plan["warnings"])


def test_accepted_is_not_assumed_when_not_printed():
    grn = _grn(items=[{"description": "Laptop", "quantity_received": 5}])
    plan = ep.plan_grn_prefill(grn, LINES)
    assert plan["lines"][0] == {"received": 5.0, "accepted": 0.0, "rejected": 0.0}
    assert any("pending inspection" in warning for warning in plan["warnings"])


def test_line_not_on_the_po_is_left_out():
    grn = _grn(items=[{"description": "Office Chair", "quantity_received": 3}])
    plan = ep.plan_grn_prefill(grn, LINES)
    assert all(line["received"] == 0.0 for line in plan["lines"])
    assert any("Office Chair" in warning for warning in plan["warnings"])


def test_longer_description_still_finds_its_po_line():
    lines = [{"description": "Laptop", "remaining_to_receive": 5.0}]
    grn = _grn(items=[{"description": "Dell Laptop 14 inch", "quantity_received": 3, "quantity_accepted": 3}])
    assert ep.plan_grn_prefill(grn, lines)["lines"][0]["received"] == 3.0


def test_unclear_date_falls_back_with_a_warning():
    plan = ep.plan_grn_prefill(_grn(received_date="mid November"), LINES)
    assert plan["received_date"] is None
    assert any("Today's date is used" in warning for warning in plan["warnings"])


def test_grn_form_values_stay_within_the_form_limits():
    grn = _grn(items=[{"description": "Laptop", "quantity_received": 9, "quantity_accepted": 9}])
    plan = ep.plan_grn_prefill(grn, LINES)
    values = grn_form_values("PO-135", plan)
    assert values["grn_create_po_select"] == "PO-135"
    assert values["grn_received_PO-135_0"] == 5.0
    assert values["grn_received_PO-135_0"] <= LINES[0]["remaining_to_receive"]
    assert values["grn_accepted_PO-135_1"] == 0.0
    assert values["grn_received_date_PO-135"] == date(2026, 11, 16)


# ---------- reading: input checks happen before any API call ----------

def test_unsupported_empty_and_oversized_files_are_refused():
    with pytest.raises(ep.DocumentReadError):
        ep.read_document("po", b"data", "po.txt")
    with pytest.raises(ep.DocumentReadError):
        ep.read_document("po", b"", "po.pdf")
    with pytest.raises(ep.DocumentReadError):
        ep.read_document("grn", b"x" * (ep.MAX_FILE_BYTES + 1), "grn.png")


# ---------- Word, Excel and JSON files ----------

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _docx():
    def p(text):
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

    def row(*cells):
        return "<w:tr>" + "".join(f"<w:tc>{p(cell)}</w:tc>" for cell in cells) + "</w:tr>"

    body = (
        p("Purchase Order")
        + "<w:p><w:r><w:t>PO Number:</w:t><w:tab/><w:t>PO-141</w:t></w:r></w:p>"
        + "<w:sdt><w:sdtContent>" + p("Vendor: Prime Office Solutions") + "</w:sdtContent></w:sdt>"
        + "<w:tbl>" + row("Description", "Qty", "Unit price") + row("Book Trolley", "6", "3,500.00") + "</w:tbl>"
    )
    return _zip({
        "word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
        "word/header1.xml": f'<w:hdr xmlns:w="{W}">{p("University Procurement Office")}</w:hdr>',
    })


def _xlsx():
    shared = ["PO Number", "PO-142", "Received date", "Description", "Qty", "Unit price", "Study Table"]
    shared_xml = f'<sst xmlns="{S}">' + "".join(f"<si><t>{t}</t></si>" for t in shared) + "</sst>"
    styles = (
        f'<styleSheet xmlns="{S}"><numFmts count="1"><numFmt numFmtId="164" formatCode="dd-mmm-yyyy"/></numFmts>'
        '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/></cellXfs></styleSheet>'
    )
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" s="1"><v>46343</v></c><c r="C2" s="2"><v>46343</v></c></row>'
        '<row r="4"><c r="A4" t="s"><v>3</v></c><c r="B4" t="s"><v>4</v></c><c r="D4" t="s"><v>5</v></c></row>'
        '<row r="5"><c r="A5" t="s"><v>6</v></c><c r="B5"><v>12</v></c><c r="D5"><v>4800.5000000000001</v></c>'
        '<c r="E5" t="inlineStr"><is><t>checked</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    return _zip({
        "xl/workbook.xml": f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="PO" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": shared_xml,
        "xl/styles.xml": styles,
        "xl/worksheets/sheet1.xml": sheet,
    })


def test_word_document_becomes_text_in_reading_order():
    lines = ep.docx_to_text(_docx()).split("\n")
    assert lines == [
        "University Procurement Office",
        "Purchase Order",
        "PO Number:\tPO-141",
        "Vendor: Prime Office Solutions",
        "Description | Qty | Unit price",
        "Book Trolley | 6 | 3,500.00",
    ]


def test_excel_workbook_becomes_rows_with_dates_and_clean_numbers():
    lines = ep.xlsx_to_text(_xlsx()).split("\n")
    assert lines[0] == "Sheet: PO"
    assert lines[1] == "PO Number\tPO-142"
    # Date cells are stored as serial numbers; both built-in and custom date styles convert.
    assert lines[2] == "Received date\t2026-11-17\t2026-11-17"
    # The empty column C is kept so quantities stay under their headings.
    assert lines[3] == "Description\tQty\t\tUnit price"
    assert lines[4] == "Study Table\t12\t\t4800.5\tchecked"


def test_json_is_validated_and_kept_as_text():
    text = ep.json_to_text(b'\xef\xbb\xbf{"orderNo": "PO-143", "lines": [{"qty": 2}]}')
    assert json.loads(text) == {"orderNo": "PO-143", "lines": [{"qty": 2}]}
    with pytest.raises(ep.DocumentReadError):
        ep.json_to_text(b"{not json")


@pytest.mark.parametrize("name, data", [
    ("po.docx", b"this is not a zip archive"),
    ("po.xlsx", b"this is not a zip archive"),
    ("po.docx", None),      # a zip without word/document.xml
    ("po.xlsx", None),      # a zip without any worksheet
    ("po.doc", b"old binary word file"),
    ("po.xls", b"old binary excel file"),
])
def test_damaged_wrong_or_legacy_office_files_are_reported(name, data):
    if data is None:
        data = _zip({"readme.txt": "nothing useful"})
    with pytest.raises(ep.DocumentReadError):
        ep.read_document("po", data, name)


def test_office_xml_with_entity_declarations_is_refused():
    bomb = _zip({
        "word/document.xml": '<!DOCTYPE x [<!ENTITY a "aaaa">]>'
                             f'<w:document xmlns:w="{W}"><w:body/></w:document>',
    })
    with pytest.raises(ep.DocumentReadError):
        ep.docx_to_text(bomb)


def test_empty_document_is_reported_before_any_api_call():
    empty = _zip({"word/document.xml": f'<w:document xmlns:w="{W}"><w:body/></w:document>'})
    with pytest.raises(ep.DocumentReadError, match="No text was found"):
        ep.read_document("po", empty, "blank.docx")


def test_text_formats_are_sent_to_the_model_as_text(monkeypatch):
    """Word / Excel / JSON reach Gemini as text with the same prompt."""
    sent = []

    class _Models:
        def generate_content(self, model, contents, config):
            sent.append((model, contents, config))
            return types.SimpleNamespace(text=json.dumps({
                "document_type": "purchase_order",
                "po_number": "PO-142",
                "items": [{"description": "Study Table", "quantity": 12, "unit_price": 4800.5}],
            }))

    reader = types.ModuleType("extract_invoice")
    reader.client = types.SimpleNamespace(models=_Models())
    reader.MODELS = ["test-model"]
    reader.MAX_ATTEMPTS = 1
    reader.WAIT_SECONDS = [0]
    monkeypatch.setitem(sys.modules, "extract_invoice", reader)

    data, model = ep.read_document("po", _xlsx(), "order.XLSX")

    assert model == "test-model"
    assert data["po_number"] == "PO-142"
    assert data["items"][0]["unit_price"] == 4800.5

    (_, contents, config), = sent
    assert config == {"response_mime_type": "application/json"}
    assert len(contents) == 1 and isinstance(contents[0], str)
    assert "It should be a purchase order" in contents[0]
    assert "Study Table\t12" in contents[0]
    assert "<<<DOCUMENT" in contents[0]
