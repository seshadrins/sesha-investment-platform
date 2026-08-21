from datetime import date
import pandas as pd
import streamlit as st
from common import api_get, api_post, api_patch, render_sidebar

render_sidebar(); st.title("IPO Lifecycle")
st.caption("Official offer-document research before listing and limited-history monitoring through day 365.")
if st.button("Discover official SEBI public-issue filings"):
    result = api_post("/ipos/discover", timeout=120)
    if result: st.success(f"Created {result['created']} and refreshed {result['updated']} IPO records."); st.rerun()

with st.expander("Add an official issue manually"):
    with st.form("manual_ipo"):
        name = st.text_input("Company legal name"); board = st.selectbox("Board", ["MAINBOARD", "SME"])
        source = st.text_input("Official HTTPS source URL"); submitted = st.form_submit_button("Add IPO")
    if submitted and api_post("/ipos", json={"company_name": name, "board": board,
            "source_url": source, "discovered_on": date.today().isoformat()}): st.rerun()

issues = api_get("/ipos")
if not issues: st.info("No IPOs discovered yet."); st.stop()
pre = [x for x in issues if x["stage"] not in {"LISTED", "POST_LISTING_MONITORING", "GRADUATED"}]
post = [x for x in issues if x["stage"] in {"LISTED", "POST_LISTING_MONITORING", "GRADUATED"}]
top = st.tabs([f"Pre-IPO ({len(pre)})", f"Post-listing 0–12 months ({len(post)})"])
for tab, rows in zip(top, (pre, post)):
    with tab:
        st.dataframe(pd.DataFrame([{"Company": x["company_name"], "Board": x["board"],
            "Stage": x["stage"], "DRHP": x["drhp_date"], "RHP": x["rhp_date"],
            "Issue open": x["issue_open_date"], "Listing": x["listing_date"],
            "Outcome": x["analysis_outcome"], "Review": x["analysis_status"],
            "Official source": x["source_url"]} for x in rows]), width="stretch", hide_index=True,
            column_config={"Official source": st.column_config.LinkColumn("Official source")})

options = {f"{x['company_name']} · {x['stage']} · {x['board']}": x for x in issues}
issue = options[st.selectbox("IPO to manage", list(options))]; ipo_id = issue["id"]
docs = api_get(f"/ipos/{ipo_id}/documents")
tabs = st.tabs(["Issue details", "Offer documents & analysis", "Post-listing monitoring"])
with tabs[0]:
    st.link_button("Open official source", issue["source_url"])
    instrument_labels = {f"{x['exchange']}:{x['symbol']} — {x['company_name']}": x["id"] for x in api_get("/instruments")}
    with st.form("ipo_details"):
        stages=["DISCOVERED","DRHP_FILED","RHP_FILED","PRICE_BAND_ANNOUNCED","ISSUE_OPEN","ISSUE_CLOSED","ALLOTMENT","LISTED","POST_LISTING_MONITORING","GRADUATED","WITHDRAWN","POSTPONED","EXPIRED"]
        stage = st.selectbox("Lifecycle stage", stages, index=stages.index(issue["stage"]) if issue["stage"] in stages else 0)
        board = st.selectbox("Board", ["UNCLASSIFIED", "MAINBOARD", "SME"], index=["UNCLASSIFIED","MAINBOARD","SME"].index(issue["board"]))
        linked_options=["None",*instrument_labels]; current_link=next((label for label,value in instrument_labels.items() if value==issue["instrument_id"]),"None")
        c1,c2,c3 = st.columns(3); symbol=c1.text_input("Listed symbol", value=issue["symbol"] or ""); exchange=c2.selectbox("Exchange", ["NSE","BSE"], index=1 if issue["exchange"]=="BSE" else 0); linked=c3.selectbox("Linked listed instrument", linked_options, index=linked_options.index(current_link))
        d1,d2,d3=st.columns(3)
        open_date=d1.date_input("Issue open", value=date.fromisoformat(issue["issue_open_date"]) if issue["issue_open_date"] else None)
        close_date=d2.date_input("Issue close", value=date.fromisoformat(issue["issue_close_date"]) if issue["issue_close_date"] else None)
        listing_date=d3.date_input("Listing", value=date.fromisoformat(issue["listing_date"]) if issue["listing_date"] else None)
        p1,p2,p3=st.columns(3)
        # value=None (not 0) so a field left unset stays None instead of silently becoming
        # a real 0 price/lot on save.
        low=p1.number_input("Price band low", min_value=0.0, value=float(issue["price_band_low"]) if issue["price_band_low"] is not None else None)
        high=p2.number_input("Price band high", min_value=0.0, value=float(issue["price_band_high"]) if issue["price_band_high"] is not None else None)
        price=p3.number_input("Final issue price", min_value=0.0, value=float(issue["issue_price"]) if issue["issue_price"] is not None else None)
        lot=st.number_input("Market lot", min_value=0, value=int(issue["lot_size"]) if issue["lot_size"] is not None else None); save=st.form_submit_button("Save verified issue details")
    if save:
        body={"stage":stage,"board":board,"symbol":symbol or None,"exchange":exchange,
              "instrument_id":None if linked=="None" else instrument_labels[linked],
              "issue_open_date":open_date.isoformat() if open_date else None,
              "issue_close_date":close_date.isoformat() if close_date else None,
              "listing_date":listing_date.isoformat() if listing_date else None,
              "price_band_low":low, "price_band_high":high,
              "issue_price":price, "lot_size":lot}
        if api_patch(f"/ipos/{ipo_id}", json=body): st.success("Issue details saved."); st.rerun()
with tabs[1]:
    with st.form("ipo_document"):
        doc_type = st.selectbox("Document type", ["DRHP", "RHP", "PROSPECTUS", "ADDENDUM"])
        doc_date = st.date_input("Document date"); title = st.text_input("Title")
        doc_source = st.text_input("Official document HTTPS URL"); upload = st.file_uploader("Offer document PDF", type=["pdf"])
        store = st.form_submit_button("Store cited document")
    if store and upload:
        result = api_post(f"/ipos/{ipo_id}/documents", data={"document_type": doc_type,
            "document_date": doc_date.isoformat(), "title": title, "source_url": doc_source},
            files={"file": (upload.name, upload.getvalue(), "application/pdf")}, timeout=180)
        if result: st.rerun()
    if docs:
        st.dataframe(pd.DataFrame(docs), width="stretch", hide_index=True)
        selected_doc = st.selectbox("Document to analyze", docs, format_func=lambda x: f"{x['document_type']} · {x['title']}")
        if st.button("Generate cited IPO draft"):
            if api_post(f"/ipos/{ipo_id}/documents/{selected_doc['id']}/analyze", json={"force": False}, timeout=300): st.rerun()
        analysis = api_get(f"/ipos/{ipo_id}/analysis") if issue["analysis_outcome"] else None
        if analysis:
            st.subheader(f"Draft outcome: {analysis['outcome']} · {analysis['status']}")
            for key, value in analysis["payload"].items():
                if key == "proposed_outcome": continue
                st.markdown(f"**{key.replace('_', ' ').title()}**")
                claims = value if isinstance(value, list) else [value]
                for claim in claims: st.write(f"• {claim['text']} [sections {', '.join(map(str, claim['section_ids']))}]")
            with st.expander("Citation evidence"):
                for sid, citation in analysis["citations"].items():
                    st.markdown(f"**Section {sid} · page {citation['page_number']}**"); st.write(citation["excerpt"])
            note = st.text_input("Review note")
            a, r = st.columns(2)
            if a.button("Accept IPO draft") and api_post(f"/ipo-analyses/{analysis['id']}/review", json={"decision":"ACCEPTED","note":note or None}): st.rerun()
            if r.button("Reject IPO draft") and api_post(f"/ipo-analyses/{analysis['id']}/review", json={"decision":"REJECTED","note":note or None}): st.rerun()
with tabs[2]:
    monitoring = api_get(f"/ipos/{ipo_id}/monitoring")
    if monitoring["status"] in {"NOT_LISTED", "WAITING_FOR_PRICE"}:
        st.info(f"Status: {monitoring['status'].replace('_', ' ').title()}")
    else:
        mcols = st.columns(4)
        mcols[0].metric("Days since listing", monitoring.get("days_since_listing"))
        mcols[1].metric("Latest price", f"₹{monitoring['latest_price']:.2f}"
                         if monitoring.get("latest_price") is not None else "—")
        mcols[2].metric("Return from issue", f"{monitoring['return_from_issue'] * 100:.2f}%"
                         if monitoring.get("return_from_issue") is not None else "—")
        mcols[3].metric("Max drawdown from issue", f"{monitoring['maximum_drawdown_from_issue'] * 100:.2f}%"
                         if monitoring.get("maximum_drawdown_from_issue") is not None else "—")
        milestones = monitoring.get("milestones") or []
        if milestones:
            st.dataframe(pd.DataFrame([{
                "Days": m["days"], "Target date": m["target_date"], "Status": m["status"],
                "Observed date": m["observed_date"],
                "Return from issue": m["return_from_issue"] * 100 if m["return_from_issue"] is not None else None,
            } for m in milestones]), width="stretch", hide_index=True, column_config={
                "Return from issue": st.column_config.NumberColumn(format="%.2f%%"),
            })
        for limitation in monitoring.get("limitations", []):
            st.caption(f"• {limitation}")
    st.warning("First-year evidence is limited. Verify issue-price, adjusted prices, proceeds use, lock-ins, and corporate actions before acting.")
