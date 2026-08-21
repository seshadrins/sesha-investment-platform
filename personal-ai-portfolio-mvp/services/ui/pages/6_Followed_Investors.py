from datetime import date

import pandas as pd
import streamlit as st

from common import api_get, api_post, disclosure_document_url, instrument_label, render_sidebar


def latest_due_quarter() -> date:
    today = date.today()
    quarter = (today.month - 1) // 3
    if quarter == 0:
        return date(today.year - 1, 12, 31)
    month = quarter * 3
    day = 30 if month in {6, 9} else 31
    return date(today.year, month, day)


render_sidebar()
st.title("Followed Investor Signals")
st.caption(
    "Dated public-disclosure evidence—not live trades, endorsements, or copy-trading instructions."
)

profiles = api_get("/followed-investors")
signals = api_get("/investor-signals")
pipeline = api_get("/investor-disclosures/status")
automation = api_get("/analysis-schedule")
coverage = next((item.get("last_result") for item in automation["jobs"]
                 if item["id"] == "investor_disclosures"), None)
reviews = api_get("/investor-disclosures/reviews?status=PENDING")
notifications = api_get("/notifications?limit=100")
activity = signals["activity"]

top = st.columns(6)
top[0].metric("Followed investors", len(profiles["profiles"]))
top[1].metric("Companies disclosed", len(signals["rows"]))
top[2].metric("Observations", sum(item["disclosures"] for item in profiles["profiles"]))
top[3].metric("Pending aliases", len(reviews))
top[4].metric("Unread alerts", pipeline["unread_notifications"])
latest_period = max((item["report_date"] for item in activity), default=None)
top[5].metric("Latest period", latest_period or "No data")

if activity and any(item["stale"] for item in activity):
    st.warning("One or more latest holdings are stale. Check the reporting date before acting.")
if reviews:
    st.warning(
        f"{len(reviews)} possible investor-name match(es) need review before they become evidence."
    )
if coverage and coverage.get("coverage_status"):
    progress = coverage.get("coverage_progress", {})
    status = coverage["coverage_status"]
    checked, active = progress.get("checked_mappings", 0), progress.get("active_mappings", 0)
    detail = f"{checked} of {active} active source mappings checked for this reporting period."
    if status == "ACTION_REQUIRED": st.error(f"Disclosure coverage: {status}. {coverage['message']} {detail}")
    # Pending alias reviews are routine, self-clearing work, not a failure — calmer framing
    # and a non-red info box instead of being badged the same as a real pipeline failure.
    elif status == "REVIEW_PENDING": st.info(f"Disclosure coverage: {coverage['message']} {detail}")
    elif status == "IN_PROGRESS": st.info(f"Disclosure coverage: {status}. {coverage['message']} {detail}")
    elif status == "NO_ATTRIBUTABLE_DISCLOSURE": st.warning(f"Disclosure coverage: {status}. {coverage['message']}")
    else: st.success(f"Disclosure coverage: {status}. {coverage['message']}")
    batch_size = pipeline["batch_size"]
    if active and batch_size:
        runs_to_complete = -(-active // batch_size)  # ceiling division
        st.caption(
            f"{checked}/{active} mappings checked this cycle · ~{runs_to_complete} scheduled "
            f"runs to complete a full pass at {batch_size}/run — by design, to avoid "
            "hammering NSE/BSE with requests."
        )

tabs = st.tabs([
    "Stock × investor matrix", "Activity feed", "Automated ingestion", "Alias review",
    "Notifications", "CSV recovery", "Configuration & limits",
])

with tabs[0]:
    st.write(
        "Stocks are rows and followed investors are columns. A blank cell means no attributable "
        "disclosure has passed validation."
    )
    matrix_rows = []
    for row in signals["rows"]:
        display = {
            "View": row["universe"].title(),
            "Stock": f"{row['exchange']}:{row['symbol']}",
            "Company": row["company_name"],
        }
        for profile in signals["profiles"]:
            cell = row["investors"][profile["id"]]
            if cell:
                stale = " · STALE" if cell["stale"] else ""
                display[profile["name"]] = (
                    f"{cell['signal'].replace('_', ' ')} · {cell['ownership_pct']:.2f}%{stale}"
                )
            else:
                display[profile["name"]] = "—"
        matrix_rows.append(display)
    matrix_df = pd.DataFrame(matrix_rows)
    if matrix_df.empty:
        st.info("No validated investor disclosures are stored yet.")
    else:
        view_filter = st.multiselect(
            "Portfolio view", ["Owned", "Prospective", "Research"],
            default=["Owned", "Prospective", "Research"],
        )
        visible = matrix_df[matrix_df["View"].isin(view_filter)]
        st.dataframe(visible, width="stretch", hide_index=True, height=500)
        st.download_button(
            "Download investor matrix", visible.to_csv(index=False).encode("utf-8"),
            file_name="followed_investor_matrix.csv", mime="text/csv",
        )

with tabs[1]:
    if not activity:
        st.info("The activity feed appears after a disclosure passes validation.")
    else:
        options = ["NEW DISCLOSURE", "INCREASED", "UNCHANGED", "REDUCED", "EXIT REPORTED"]
        selected_signals = st.multiselect("Activity", options, default=options)
        activity_df = pd.DataFrame([{
            "Reported period": item["report_date"], "Filed on": item["filed_on"],
            "Investor": item["investor_name"],
            "Stock": f"{item['exchange']}:{item['symbol']}",
            "Company": item["company_name"], "View": item["universe"].title(),
            "Signal": item["signal"].replace("_", " "),
            "Ownership": item["ownership_pct"], "Change": item["change_percentage_points"],
            "Stale": item["stale"], "Source": item["source_url"],
            "Stored copy": disclosure_document_url(item["document_id"])
                           if item.get("document_id") else None,
        } for item in activity])
        visible = activity_df[activity_df["Signal"].isin(selected_signals)]
        st.dataframe(visible, width="stretch", hide_index=True, column_config={
            "Ownership": st.column_config.NumberColumn(format="%.2f%%"),
            "Change": st.column_config.NumberColumn(
                "Change (percentage points)", format="%+.2f"
            ),
            "Source": st.column_config.LinkColumn("Source filing", display_text="Open"),
            "Stored copy": st.column_config.LinkColumn("Stored copy", display_text="View"),
        })

with tabs[2]:
    st.subheader("Exchange filing ingestion")
    st.write(
        "The 06:00 workflow checks official NSE/BSE shareholding filings after the quarterly "
        "filing lag. Deterministic XBRL parsing runs first; an LLM is only a validated fallback."
    )
    status_cols = st.columns(5)
    status_cols[0].metric("Active mappings", pipeline["mappings"])
    status_cols[1].metric("Parsed documents", pipeline["documents"].get("PARSED", 0))
    status_cols[2].metric("Parser failures", pipeline["documents"].get("PARSER_FAILED", 0))
    status_cols[3].metric("Batch size", pipeline["batch_size"])
    status_cols[4].metric("Parser version", pipeline["parser_version"])

    period = st.date_input("Reporting quarter", value=latest_due_quarter())
    force = st.checkbox(
        "Force re-download and reprocess cached filings",
        help="Use only for a revised filing, parser upgrade, or recovery investigation.",
    )
    if st.button("Run disclosure ingestion now", type="primary"):
        result = api_post(
            "/investor-disclosures/ingest",
            json={"period": period.isoformat(), "force": force}, timeout=300,
        )
        if result:
            if result["status"] == "FAILED":
                st.error("All attempted source checks failed. Review the errors and source mappings.")
            elif result["status"] == "PARTIAL":
                st.warning("The batch completed with some source failures.")
            else:
                st.success("The disclosure batch completed.")
            result_cols = st.columns(5)
            result_cols[0].metric("Checked", result.get("checked", 0))
            result_cols[1].metric("Discovered", result.get("discovered", 0))
            result_cols[2].metric("Processed", result.get("processed", 0))
            result_cols[3].metric("Matched", result.get("matched", 0))
            result_cols[4].metric("Reviews created", result.get("reviews_created", 0))
            if result.get("errors"):
                with st.expander(f"Errors ({len(result['errors'])})"):
                    for error in result["errors"]:
                        st.write(f"• {error.get('error', error) if isinstance(error, dict) else error}")
            st.rerun()

    st.divider()
    st.subheader("Exchange source mappings")
    st.caption(
        "NSE symbols are created automatically. Add a six-digit BSE scrip code for a cross-listed "
        "stock when NSE blocks unattended access or when BSE is the preferred source."
    )
    mappings = api_get("/investor-disclosures/mappings")
    if mappings:
        st.dataframe(pd.DataFrame(mappings), width="stretch", hide_index=True, height=350)
    instruments = api_get("/instruments")
    if instruments:
        labels = {instrument_label(item): item["id"] for item in instruments}
        with st.form("source_mapping"):
            selected = st.selectbox("Stock", list(labels))
            exchange = st.selectbox("Filing exchange", ["BSE", "NSE"])
            source_code = st.text_input(
                "Exchange source code",
                help="BSE: six-digit scrip code. NSE: official trading symbol.",
            )
            submitted = st.form_submit_button("Save source mapping")
        if submitted:
            result = api_post("/investor-disclosures/mappings", json={
                "instrument_id": labels[selected], "exchange": exchange,
                "source_code": source_code, "active": True,
            })
            if result:
                st.success("Source mapping saved.")
                st.rerun()

with tabs[3]:
    st.subheader("Investor-name review queue")
    st.write(
        "Fuzzy or ambiguous names never become holdings automatically. Approving a match stores "
        "the observed alias for later filings and creates the validated disclosure; rejecting it "
        "does neither."
    )
    if not reviews:
        st.success("No investor aliases need review.")
    profile_options = {item["name"]: item["id"] for item in profiles["profiles"]}
    for review in reviews:
        with st.container(border=True):
            st.markdown(f"**{review['observed_name']}** in {review['company_name']}")
            st.caption(
                f"{review['stock']} · {review['report_date']} · "
                f"{review['ownership_pct']:.4f}% · confidence {review['confidence']:.0%}"
            )
            filing_links = st.columns([1, 1, 4])
            filing_links[0].link_button("Open source filing", review["source_url"])
            if review.get("document_id"):
                filing_links[1].link_button(
                    "View stored copy", disclosure_document_url(review["document_id"])
                )
            default_id = review["proposed_investor_id"]
            names = list(profile_options)
            default_index = next(
                (index for index, name in enumerate(names) if profile_options[name] == default_id), 0
            )
            selected_profile = st.selectbox(
                "Matched profile", names, index=default_index, key=f"profile_{review['id']}"
            )
            note = st.text_input("Decision note", key=f"note_{review['id']}")
            approve, reject = st.columns(2)
            if approve.button("Approve match", type="primary", key=f"approve_{review['id']}"):
                result = api_post(
                    f"/investor-disclosures/reviews/{review['id']}/decision",
                    json={"decision": "APPROVED", "investor_id": profile_options[selected_profile],
                          "note": note or None},
                )
                if result:
                    st.rerun()
            if reject.button("Reject match", key=f"reject_{review['id']}"):
                result = api_post(
                    f"/investor-disclosures/reviews/{review['id']}/decision",
                    json={"decision": "REJECTED", "note": note or None},
                )
                if result:
                    st.rerun()

with tabs[4]:
    st.subheader("Disclosure notifications")
    st.caption(
        "Every new or revised validated disclosure and every ambiguous alias is retained here. "
        "An optional HTTPS webhook can also receive the same payload."
    )
    if not notifications:
        st.info("No disclosure notifications yet.")
    for item in notifications:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            left.markdown(f"**{item['title']}**")
            left.write(item["message"])
            left.caption(
                f"{item['created_at']} · {item['severity']} · delivery {item['delivery_status']}"
            )
            source_url = item.get("payload", {}).get("source_url")
            if source_url:
                left.link_button("Open source", source_url)
            if not item["read"] and right.button("Mark read", key=f"read_{item['id']}"):
                if api_post(f"/notifications/{item['id']}/read"):
                    st.rerun()

with tabs[5]:
    st.subheader("CSV recovery import")
    st.write(
        "Use this auditable recovery path when an exchange blocks unattended access or a filing "
        "format is not yet supported. Every row requires the original HTTPS source link."
    )
    template = (
        "investor_id,exchange,symbol,company_name,isin,report_date,filed_on,"
        "ownership_pct,shares,source_url,source_type\n"
    )
    st.download_button(
        "Download CSV template", template.encode("utf-8"),
        file_name="investor_disclosures_template.csv", mime="text/csv",
    )
    upload = st.file_uploader("Disclosure CSV", type=["csv"])
    if st.button("Validate and import", disabled=upload is None):
        result = api_post("/imports/investor-disclosures", files={
            "file": (upload.name, upload.getvalue(), "text/csv")
        })
        if result:
            st.success(f"Imported {result['imported']} and updated {result['updated']} disclosures.")
            st.rerun()
    with st.expander("Official source directories", expanded=True):
        st.markdown(
            "- [NSE shareholding-pattern filings](https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern)\n"
            "- [BSE shareholding-pattern filings](https://www.bseindia.com/corporates/Sharehold_Searchnew.aspx)\n"
            "- [SEBI corporate-filings directory](https://www.sebi.gov.in/curation/corporate_filings.html)"
        )

with tabs[6]:
    st.subheader("Followed profiles")
    st.caption(f"Configuration: {profiles['config_file']} · version {profiles['config_version']}")
    for profile in profiles["profiles"]:
        with st.container(border=True):
            left, right = st.columns([3, 1])
            left.subheader(profile["name"])
            left.write(profile["description"])
            left.caption("Disclosure aliases: " + ", ".join(profile["aliases"]))
            right.metric("Disclosures", profile["disclosures"])
    st.subheader("Extraction policy")
    llm = pipeline["llm"]
    st.write(f"Fallback policy: **{llm['policy']}**")
    st.write(f"Ollama model: **{llm['ollama_model']}**")
    st.write(
        f"OpenRouter: **{'configured' if llm['openrouter_configured'] else 'not configured'}** "
        f"using **{llm['openrouter_model']}**"
    )
    st.caption(
        "Random free-model routing is disabled. Model output must pass the same schema, alias, "
        "date, percentage, source, and review rules as deterministic extraction."
    )
    st.subheader("Methodology")
    st.json(signals["methodology"])
    for limitation in signals["limitations"]:
        st.write(f"• {limitation}")
    st.info(
        "The configured names are not an assertion that future decisions will be excellent. "
        "Investor activity remains corroborating evidence and cannot create a Strong Buy."
    )

st.warning("Public disclosures can be delayed or incomplete. Verify the original filing before acting.")
