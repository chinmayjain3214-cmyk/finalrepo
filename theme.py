"""Visual theme for the invoice agent Streamlit app.

Design notes
- One quiet canvas, white surfaces, hairline borders. No gradients, glass or
  drifting backgrounds: the invoice evidence is the content, not the chrome.
- Graphite sidebar with the signed-in person at the top and the workspace list
  below it. The login panel uses the same graphite so signing in feels like the
  panel folding into the sidebar.
- One accent ("ledger teal") for primary actions and the active workspace.
  Green, amber and red are reserved for check results.
- One typeface (Instrument Sans). Amounts use tabular figures so columns of
  numbers line up.
"""

import html

import streamlit as st


CSS = """
<style>

@import url('https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&display=swap');

:root {
  --graphite:#1F232B;
  --graphite-2:#2A2F39;
  --canvas:#F4F5F7;
  --surface:#FFFFFF;
  --ink:#1A1D24;
  --muted:#5D6472;
  --line:#E2E5EA;
  --accent:#0F6F62;
  --accent-strong:#0B5A50;
  --accent-soft:#E3F1EE;
  --accent-on-dark:#6FD6C0;
  --ok:#1B7F4B;
  --warn:#9A6200;
  --warn-bg:#FFF4DC;
  --bad:#B8283F;
  --font:'Instrument Sans',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
}


/* ---------- PAGE ---------- */

[data-testid='stAppViewContainer'] {
  background:var(--canvas) !important;
}

[data-testid='stMain'],
[data-testid='stMainBlockContainer'],
[data-testid='stHeader'] {
  background:transparent !important;
}

[data-testid='stAppDeployButton'],
.stDeployButton,
footer {
  display:none !important;
}

.block-container {
  max-width:1280px;
  /* Extra end space lets the final cards/expanders scroll fully into view. */
  padding:1.6rem 2.2rem 6rem;
}

/* Preserve normal vertical scrolling in Streamlit's main content area. */
[data-testid='stMain'] {
  overflow-y:auto !important;
  overflow-x:hidden !important;
}

[data-testid='stMainBlockContainer'] {
  min-height:max-content !important;
}


/* ---------- TYPOGRAPHY ---------- */

.stApp,
.stApp p,
.stApp label,
.stApp li,
.stApp input,
.stApp textarea,
.stApp button,
[data-testid='stCaptionContainer'] {
  font-family:var(--font);
}

.stApp {
  color:var(--ink);
}

h1, h2, h3, h4 {
  font-family:var(--font) !important;
  color:var(--ink);
  letter-spacing:-.02em;
}

h1 {
  font-size:2rem !important;
  font-weight:650 !important;
  line-height:1.15 !important;
  padding-top:.2rem !important;
}

h2 {
  font-size:1.4rem !important;
  font-weight:600 !important;
}

h3 {
  font-size:1.12rem !important;
  font-weight:600 !important;
}

[data-testid='stCaptionContainer'],
[data-testid='stCaptionContainer'] * {
  color:var(--muted) !important;
  opacity:1 !important;
}

input::placeholder,
textarea::placeholder {
  color:#8A919E !important;
  opacity:1 !important;
}

/* Small context line above a page title. */
.page-eyebrow {
  font-size:.84rem;
  font-weight:500;
  color:var(--muted);
  text-transform:lowercase;
  margin-bottom:2px;
}

.page-eyebrow::first-letter {
  text-transform:uppercase;
}


/* ---------- SURFACES: CARDS, METRICS, TABS ---------- */

[data-testid='stVerticalBlockBorderWrapper'] {
  background:var(--surface) !important;
  border:1px solid var(--line) !important;
  border-radius:10px !important;
  box-shadow:none;
}

[data-testid='stMetric'] {
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:10px;
  padding:16px 18px;
}

[data-testid='stMetricLabel'],
[data-testid='stMetricLabel'] p {
  color:var(--muted) !important;
  font-size:.84rem !important;
  font-weight:500;
}

[data-testid='stMetricValue'] {
  font-family:var(--font);
  font-weight:650;
  color:var(--ink);
  font-variant-numeric:tabular-nums;
  letter-spacing:-.02em;
}

/* Amount tiles (finance workspace). The value shrinks to fit narrow
   columns; if it still does not fit it is cut with "…" and the full
   amount shows on hover. */
.amount-tile {
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:10px;
  padding:16px 18px;
  min-width:0;
  height:100%;
  box-sizing:border-box;
  /* Lets the value size itself to this tile's own width. */
  container-type:inline-size;
}

.amount-tile .label {
  color:var(--muted);
  font-size:.84rem;
  font-weight:500;
  margin-bottom:4px;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}

.amount-tile:hover .value,
.amount-tile:focus .value,
.amount-tile:focus-within .value,
.amount-tile:hover .label,
.amount-tile:focus .label,
.amount-tile:focus-within .label {
  /* Hover or keyboard focus: let a cut-off amount wrap so it shows in full. */
  white-space:normal;
  overflow:visible;
  text-overflow:clip;
  overflow-wrap:anywhere;
}

.amount-tile:focus-visible {
  outline:2px solid var(--accent);
  outline-offset:2px;
}

.amount-tile .value {
  color:var(--ink);
  font-weight:650;
  font-size:clamp(1.05rem, 1.7vw, 1.75rem);
  /* Browsers with container queries fit ~13 characters to the tile width. */
  font-size:clamp(.95rem, 11cqi, 1.75rem);
  line-height:1.2;
  letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  cursor:help;
}

/* Tabs: plain text with an underline on the active one. */
[data-baseweb='tab-list'] {
  gap:4px;
  background:transparent;
  padding:0;
  border-bottom:1px solid var(--line);
}

button[data-baseweb='tab'] {
  background:transparent;
  border-radius:0;
  padding:10px 14px;
  height:auto;
  color:var(--muted);
}

button[data-baseweb='tab']:hover {
  color:var(--ink);
}

button[data-baseweb='tab'][aria-selected='true'] {
  color:var(--ink);
  font-weight:600;
  box-shadow:inset 0 -2px 0 var(--accent);
}

[data-baseweb='tab-highlight'],
[data-baseweb='tab-border'] {
  display:none;
}

[data-testid='stAlert'] {
  border-radius:8px;
  border:1px solid var(--line);
}

[data-testid='stExpander'] {
  border:1px solid var(--line) !important;
  border-radius:10px;
  background:var(--surface);
}

[data-testid='stDataFrame'] {
  border:1px solid var(--line);
  border-radius:10px;
  overflow:hidden;
}

hr {
  border-color:var(--line) !important;
}


/* ---------- INPUTS AND UPLOADER ---------- */

div[data-baseweb='input'] > div,
div[data-baseweb='select'] > div,
div[data-baseweb='textarea'] {
  background:var(--surface);
  border-radius:8px;
  border:1px solid var(--line);
}

div[data-baseweb='input']:focus-within > div,
div[data-baseweb='textarea']:focus-within {
  border-color:var(--accent);
  box-shadow:0 0 0 3px rgba(15,111,98,.16);
}

[data-testid='stFileUploaderDropzone'] {
  background:var(--surface);
  border:1.5px dashed #C4C9D2;
  border-radius:10px;
  padding:28px;
  transition:border-color .15s, background .15s;
}

[data-testid='stFileUploaderDropzone']:hover {
  border-color:var(--accent);
  background:var(--accent-soft);
}

div[data-testid='stForm'] {
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:12px;
  padding:26px;
  box-shadow:none;
}


/* ---------- BUTTONS ---------- */

button[kind='primary'],
[data-testid='stBaseButton-primary'],
[data-testid='stBaseButton-primaryFormSubmit'] {
  background:var(--accent) !important;
  color:#fff !important;
  border:1px solid var(--accent) !important;
  border-radius:8px !important;
  font-weight:600 !important;
  min-height:42px;
  box-shadow:none !important;
  transition:background .15s;
}

button[kind='primary']:hover,
[data-testid='stBaseButton-primary']:hover,
[data-testid='stBaseButton-primaryFormSubmit']:hover {
  background:var(--accent-strong) !important;
  border-color:var(--accent-strong) !important;
}

[data-testid='stBaseButton-secondary'] {
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:8px;
  min-height:42px;
  font-weight:600;
  color:var(--ink);
}

[data-testid='stBaseButton-secondary']:hover {
  border-color:var(--accent);
  color:var(--accent);
}

.stApp button:focus-visible {
  outline:2px solid var(--accent);
  outline-offset:2px;
}


/* ---------- SIDEBAR ---------- */

[data-testid='stSidebar'] {
  background:var(--graphite) !important;
  border-right:none;
}

[data-testid='stSidebar'] [data-testid='stMarkdownContainer'],
[data-testid='stSidebar'] [data-testid='stMarkdownContainer'] p {
  color:#E8EAEE !important;
}

[data-testid='stSidebar'] [data-testid='stCaptionContainer'],
[data-testid='stSidebar'] [data-testid='stCaptionContainer'] * {
  color:#9BA2AF !important;
}

[data-testid='stSidebar'] hr {
  border-color:rgba(255,255,255,.10) !important;
}

.side-brand {
  font-size:1.02rem;
  font-weight:650;
  letter-spacing:-.01em;
  color:#FFFFFF;
  display:flex;
  align-items:center;
  gap:10px;
  margin:2px 0 22px;
}

/* A small invoice glyph: a sheet with two ruled lines. */
.side-brand .glyph {
  flex:none;
  width:20px;
  height:24px;
  border-radius:4px;
  background:
    linear-gradient(var(--graphite),var(--graphite)) 5px 8px / 10px 2px no-repeat,
    linear-gradient(var(--graphite),var(--graphite)) 5px 13px / 7px 2px no-repeat,
    var(--accent-on-dark);
}

.side-profile {
  display:flex;
  align-items:center;
  gap:12px;
  padding:14px 12px;
  border-radius:10px;
  background:var(--graphite-2);
  margin-bottom:22px;
}

.side-avatar {
  flex:none;
  width:40px;
  height:40px;
  border-radius:50%;
  display:grid;
  place-items:center;
  background:var(--accent-on-dark);
  color:var(--graphite);
  font-weight:700;
  font-size:.92rem;
}

.side-name {
  color:#FFFFFF;
  font-weight:600;
  line-height:1.25;
  overflow-wrap:anywhere;
}

.side-role {
  color:#9BA2AF;
  font-size:.84rem;
  line-height:1.3;
}

.side-heading {
  color:#9BA2AF;
  font-size:.8rem;
  font-weight:500;
  margin:0 0 6px 12px;
}

/* Workspace shown as a nav row (single-workspace roles). */
.side-nav-item {
  padding:9px 12px;
  border-radius:8px;
  border-left:3px solid var(--accent-on-dark);
  background:rgba(255,255,255,.08);
  color:#FFFFFF;
  font-weight:600;
}

/* Workspace picker (multi-workspace roles): radio rows styled as nav rows. */
[data-testid='stSidebar'] [data-testid='stRadio'] [role='radiogroup'] {
  gap:2px;
}

[data-testid='stSidebar'] [data-testid='stRadio'] label[data-baseweb='radio'] {
  width:100%;
  margin:0;
  padding:9px 12px;
  border-radius:8px;
  border-left:3px solid transparent;
  cursor:pointer;
}

[data-testid='stSidebar'] [data-testid='stRadio'] label[data-baseweb='radio']:hover {
  background:rgba(255,255,255,.05);
}

[data-testid='stSidebar'] [data-testid='stRadio'] label[data-baseweb='radio']:has(input:checked) {
  background:rgba(255,255,255,.08);
  border-left-color:var(--accent-on-dark);
}

[data-testid='stSidebar'] [data-testid='stRadio'] label[data-baseweb='radio'] p {
  color:#D5D9E0 !important;
}

[data-testid='stSidebar'] [data-testid='stRadio'] label[data-baseweb='radio']:has(input:checked) p {
  color:#FFFFFF !important;
  font-weight:600;
}

/* Hide the radio dot, but only when that element is not the label text. */
[data-testid='stSidebar'] [data-testid='stRadio'] label[data-baseweb='radio'] > div:first-child:not(:has([data-testid='stMarkdownContainer'])) {
  display:none;
}

[data-testid='stSidebar'] button {
  background:transparent !important;
  color:#D5D9E0 !important;
  border:1px solid rgba(255,255,255,.16) !important;
  border-radius:8px;
  box-shadow:none !important;
}

[data-testid='stSidebar'] button:hover {
  background:rgba(255,255,255,.08) !important;
  color:#FFFFFF !important;
}

[data-testid='stSidebar'] button:focus-visible {
  outline:2px solid var(--accent-on-dark);
  outline-offset:2px;
}


/* ---------- UPLOAD PAGE HELPERS ---------- */

.upload-step {
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:10px;
  padding:12px 16px;
}

.upload-step b {
  color:var(--ink);
}

.upload-step small {
  color:var(--muted);
}


/* ---------- LOGIN ---------- */

[data-testid='stHorizontalBlock']:has(.login-hero) {
  align-items:center;
}

.login-hero {
  border-radius:14px;
  padding:40px;
  min-height:540px;
  color:#fff;
  background:var(--graphite);
  display:flex;
  flex-direction:column;
}

.login-hero .side-brand {
  margin-bottom:auto;
  padding-bottom:36px;
}

.login-hero h1 {
  color:#fff !important;
  font-size:2.25rem !important;
  line-height:1.12 !important;
  font-weight:650 !important;
  letter-spacing:-.03em;
  margin:0 0 14px;
  padding:0 !important;
  max-width:16ch;
}

.login-hero .description {
  color:#B4BAC6;
  max-width:44ch;
  line-height:1.6;
  margin-bottom:30px;
}

/* A sample invoice being checked: the one moving element on the page. */
.ticket {
  background:#fff;
  color:var(--ink);
  border-radius:10px;
  padding:18px 20px;
  max-width:480px;
}

.ticket-head {
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  padding-bottom:12px;
  margin-bottom:6px;
  border-bottom:1px solid var(--line);
}

.ticket-vendor {
  font-weight:650;
}

.ticket-ref {
  font-size:.82rem;
  color:var(--muted);
  display:flex;
  gap:12px;
  font-variant-numeric:tabular-nums;
}

.ticket-total {
  font-weight:650;
  font-size:1.2rem;
  font-variant-numeric:tabular-nums;
}

.check {
  display:flex;
  align-items:center;
  gap:12px;
  padding:8px 0;
  font-size:.93rem;
  opacity:0;
  transform:translateY(4px);
  animation:checkIn .4s ease forwards;
}

.ticket .check:nth-child(2) { animation-delay:.4s; }
.ticket .check:nth-child(3) { animation-delay:.9s; }
.ticket .check:nth-child(4) { animation-delay:1.4s; }
.ticket .check:nth-child(5) { animation-delay:2.1s; }

.tick {
  flex:none;
  width:20px;
  height:20px;
  border-radius:50%;
  background:var(--ok);
  color:#fff;
  display:grid;
  place-items:center;
  font-size:.74rem;
  font-weight:700;
}

.check.hold {
  margin-top:6px;
  padding:10px 12px;
  border-radius:8px;
  background:var(--warn-bg);
  color:#7A5200;
  font-weight:600;
}

.check.hold .tick {
  background:var(--warn);
}

@keyframes checkIn {
  to {
    opacity:1;
    transform:none;
  }
}

.login-footer {
  margin-top:14px;
  font-size:.88rem;
  color:var(--muted);
}


/* ---------- RESPONSIVE ---------- */

@media (max-width:900px) {
  .login-hero {
    min-height:0;
    padding:26px;
  }

  .login-hero h1 {
    font-size:1.8rem !important;
  }

  .block-container {
    padding:1rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  .check {
    animation:none;
    opacity:1;
    transform:none;
  }
}

</style>
"""


BRAND_HTML = '<div class="side-brand"><span class="glyph"></span>Invoice agent</div>'


LOGIN_HERO_HTML = (
    """
<div class="login-hero">
  """
    + BRAND_HTML
    + """
  <h1>The AI checks the invoice. A person approves the payment.</h1>
  <div class="description">
    Upload an invoice and the agent reads it, checks the arithmetic, finds the
    purchase order and confirms the vendor. Nothing is paid until an approver signs off.
  </div>
  <div class="ticket">
    <div class="ticket-head">
      <div>
        <div class="ticket-vendor">Northwind Supply Co.</div>
        <div class="ticket-ref"><span>INV-2041</span><span>PO-7731</span></div>
      </div>
      <div class="ticket-total">4,860.00</div>
    </div>
    <div class="check"><span class="tick">✓</span><span>Line items add up to the total</span></div>
    <div class="check"><span class="tick">✓</span><span>Purchase order found</span></div>
    <div class="check"><span class="tick">✓</span><span>Vendor matches the purchase order</span></div>
    <div class="check hold"><span class="tick">!</span><span>Waiting for an approver</span></div>
  </div>
</div>
"""
)


LOGIN_FOOTER_HTML = """
<div class="login-footer">
  Every payment needs three human approvals. Sign-in pauses after repeated wrong passwords.
</div>
"""


def role_title(role):
    """Readable role name: 'department_head' -> 'Department head'."""

    return str(role or "").replace("_", " ").strip().capitalize()


def _initials(full_name):
    parts = [part for part in str(full_name or "").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def sidebar_profile_html(full_name, role):
    """Brand mark plus the signed-in person, shown at the top of the sidebar."""

    return (
        BRAND_HTML
        + '<div class="side-profile">'
        + '<div class="side-avatar">' + html.escape(_initials(full_name)) + "</div>"
        + "<div>"
        + '<div class="side-name">' + html.escape(str(full_name or "")) + "</div>"
        + '<div class="side-role">' + html.escape(role_title(role)) + "</div>"
        + "</div></div>"
        + '<div class="side-heading">Workspace</div>'
    )


def sidebar_nav_item_html(page):
    """A single, already-selected workspace row."""

    return '<div class="side-nav-item">' + html.escape(str(page)) + "</div>"


def amount_tile_html(label, value):
    """A metric tile whose full value appears on hover (title tooltip)."""

    label = html.escape(str(label))
    value = html.escape(str(value))
    return (
        '<div class="amount-tile" tabindex="0" title="' + label + ": " + value + '">'
        + '<div class="label">' + label + "</div>"
        + '<div class="value" aria-label="' + label + ": " + value + '">' + value + "</div>"
        + "</div>"
    )


def inject_theme():
    st.markdown(CSS, unsafe_allow_html=True)
