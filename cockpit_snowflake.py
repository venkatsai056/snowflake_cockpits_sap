"""
COCKPIT 1 — SAP → S3 Extraction Health
=========================================
Deploy as a Streamlit-in-Snowflake app. Reads live from INGESTION_METADATA.

HONEST LIMITATION, stated in the UI itself, not hidden:
INGESTION_METADATA only stores the LAST run per entity - there is no
run-history table yet. This cockpit shows current health accurately;
it cannot show trends over time until a history table exists (see the
banner at the bottom for this exact gap, already flagged as a project
open item).
"""

import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="SAP → S3 Extraction Cockpit", layout="wide")

# Works both inside Snowflake's own Streamlit hosting (get_active_session
# succeeds immediately) and on external Streamlit Community Cloud, where
# no active session exists and a real connection must be built from
# secrets instead (Settings -> Secrets in the Streamlit Cloud app,
# formatted as:
#   [connections.snowflake]
#   account = "..."
#   user = "..."
#   password = "..."
#   warehouse = "TWT_SAP_INTEGRATION_WH"
#   database = "SAP_SILVER"
#   schema = "TWT_SF_INFRA"
#   role = "TWT_SAP_INTEGRATION_DEVELOPER"
# ).
try:
    session = get_active_session()
except Exception:
    session = st.connection("snowflake").session()

# ============================================================
# Visual helpers — colored KPI cards + status pills, matching
# the reference dashboard's card-based look instead of plain
# st.metric() text.
# ============================================================
CARD_CSS = """
<style>
.kpi-card { border-radius: 12px; padding: 20px 22px; color: white; height: 100%; }
.kpi-label { font-size: 0.85em; opacity: 0.85; margin-bottom: 6px; }
.kpi-value { font-size: 2.1em; font-weight: 700; line-height: 1.1; }
.status-pill { padding: 4px 14px; border-radius: 14px; font-size: 0.85em; font-weight: 600; color: white; display: inline-block; }
</style>
"""
st.markdown(CARD_CSS, unsafe_allow_html=True)

COLORS = {
    "blue": "#2563eb", "purple": "#7c3aed", "green": "#16a34a",
    "gray": "#475569", "red": "#dc2626", "amber": "#d97706",
}

def kpi_card(label, value, color="blue", icon=""):
    st.markdown(
        f"""<div class="kpi-card" style="background:{COLORS[color]};">
                <div class="kpi-label">{icon} {label}</div>
                <div class="kpi-value">{value}</div>
            </div>""",
        unsafe_allow_html=True,
    )

def status_pill_html(text, color):
    return f'<span class="status-pill" style="background:{COLORS[color]};">{text}</span>'


EXTRACTION_ENTITIES = [
    "SALES_ORDER", "PURCHASE_ORDER", "SALES_RETURN_INVOICE", "PO_STO_GRN",
    "TRANSFER_INVOICE", "CANCELLED_SALES_ORDER", "PROCESS_ORDER", "BILL_OF_MATERIALS",
    "SALES_RETURN_GRN", "CREDIT_MEMO_REQUEST", "DEBIT_MEMO_REQUEST",
    "PROCESS_ORDER_CONFIRMATION", "PLANT_MASTER", "SALES_ORDER_FOC",
    "PROCESS_PRODUCTION_GRN", "CANCELLED_RETURN_SALES_ORDER",
]

st.markdown(
    """
    <div style="background:#8B1E4A;padding:28px 32px;border-radius:10px;margin-bottom:20px;">
        <h1 style="color:white;margin:0;">SAP → S3 Extraction Cockpit</h1>
        <p style="color:#f0d5df;margin:6px 0 0 0;">
            Daily Glue extraction pipelines · live from INGESTION_METADATA
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---- Filters ----
col_f1, col_f2, col_f3 = st.columns([2, 2, 1])
with col_f1:
    entity_filter = st.multiselect("Filter by entity", EXTRACTION_ENTITIES, default=[])
with col_f2:
    status_filter = st.multiselect("Filter by status", ["SUCCESS", "FAILED", "RUNNING"], default=[])
with col_f3:
    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()

entity_list_sql = "'" + "','".join(EXTRACTION_ENTITIES) + "'"
df = session.sql(f"""
    SELECT SOURCE_ENTITY, GLUE_JOB_NAME, LAST_RUN_STATUS, LAST_RUN_START_TIME,
           LAST_RUN_END_TIME, SOURCE_COUNT, EXTRACTED_COUNT, DUPLICATE_COUNT,
           CONSECUTIVE_FAILURES, LAST_ERROR_MESSAGE
    FROM SAP_SILVER.TWT_SF_INFRA.INGESTION_METADATA
    WHERE SOURCE_ENTITY IN ({entity_list_sql})
    ORDER BY LAST_RUN_START_TIME DESC
""").to_pandas()

if entity_filter:
    df = df[df["SOURCE_ENTITY"].isin(entity_filter)]
if status_filter:
    df = df[df["LAST_RUN_STATUS"].isin(status_filter)]

# ---- Top metrics, colored cards ----
total = len(EXTRACTION_ENTITIES)
success_count = (df["LAST_RUN_STATUS"] == "SUCCESS").sum()
failing = df[df["LAST_RUN_STATUS"] == "FAILED"]
stuck = df[df["LAST_RUN_STATUS"] == "RUNNING"]
total_extracted_today = int(df["EXTRACTED_COUNT"].fillna(0).sum())

m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    kpi_card("Pipelines Tracked", total, "blue", "🗂️")
with m2:
    kpi_card("Succeeded (latest)", f"{success_count}/{total}", "green", "✅")
with m3:
    kpi_card("Currently Failing", len(failing), "red" if len(failing) else "gray", "🔴")
with m4:
    kpi_card("Stuck / Running", len(stuck), "amber" if len(stuck) else "gray", "⏳")
with m5:
    kpi_card("Records Extracted", f"{total_extracted_today:,}", "purple", "📦")

st.markdown("<br>", unsafe_allow_html=True)
st.divider()

# ---- Failing pipelines get their own loud section, not buried in a table ----
if len(failing) > 0:
    st.markdown("### 🔴 Needs Attention")
    for _, row in failing.iterrows():
        with st.expander(f"❌ {row['SOURCE_ENTITY']} — failed at {row['LAST_RUN_START_TIME']}", expanded=True):
            st.write(f"**Job:** {row['GLUE_JOB_NAME']}")
            st.write(f"**Consecutive failures:** {row['CONSECUTIVE_FAILURES']}")
            st.code(row["LAST_ERROR_MESSAGE"] or "(no error message captured)", language=None)

# ---- Full status table, colored status pills instead of plain text ----
st.markdown("### All Pipelines — Latest Run")

# FIXED (was a workaround): extraction and the S3 -> Snowflake Silver
# load jobs now write to DISTINCT INGESTION_METADATA rows
# (<ENTITY>_SILVER_LOAD for the load side) - see
# bootstrap_silver_load_tracking.sql. This table's own SOURCE_COUNT/
# EXTRACTED_COUNT are no longer touched by any load job, so every
# entity's numbers here are genuinely extraction-only again. No more
# special-casing needed.

status_color_map = {"SUCCESS": "green", "FAILED": "red", "RUNNING": "amber"}
display_df = df.copy()
display_df["Status"] = display_df["LAST_RUN_STATUS"].apply(
    lambda s: status_pill_html(s, status_color_map.get(s, "gray"))
)
display_df["Counts Match"] = display_df.apply(
    lambda r: status_pill_html("✔ MATCH", "green") if pd.notna(r["SOURCE_COUNT"]) and r["SOURCE_COUNT"] == r["EXTRACTED_COUNT"]
    else status_pill_html("—", "gray"),
    axis=1,
)
st.write(
    display_df[["SOURCE_ENTITY", "Status", "LAST_RUN_START_TIME", "SOURCE_COUNT", "EXTRACTED_COUNT", "Counts Match"]]
    .rename(columns={
        "SOURCE_ENTITY": "Entity", "LAST_RUN_START_TIME": "Last Run",
        "SOURCE_COUNT": "SAP Count", "EXTRACTED_COUNT": "Extracted",
    })
    .to_html(escape=False, index=False),
    unsafe_allow_html=True,
)

st.divider()
st.info(
    "**Known limitation:** this table shows the *latest* run only — INGESTION_METADATA "
    "does not yet store run history, so trend charts (e.g. \"success rate over the last "
    "30 days\") aren't possible until a history/run-log table is added. Flagged as an "
    "open project item, not a bug in this cockpit."
)
