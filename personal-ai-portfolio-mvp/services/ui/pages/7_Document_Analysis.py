from datetime import date
import pandas as pd
import streamlit as st
from common import api_get, api_post, instrument_label, render_sidebar

render_sidebar()
st.title("Grounded Document Analysis")
st.caption("Import annual reports or transcripts and create reviewable drafts grounded in stored sections.")
instruments, documents = api_get("/instruments"), api_get("/documents")

with st.expander("Import a document", expanded=not documents):
    if not instruments:
        st.info("Add a company before importing a document.")
    else:
        labels = {instrument_label(item): item["id"] for item in instruments}
        with st.form("document_upload"):
            stock = st.selectbox("Company", list(labels))
            kind = st.selectbox("Document type", ["ANNUAL_REPORT", "TRANSCRIPT"],
                                format_func=lambda value: value.replace("_", " ").title())
            title = st.text_input("Title")
            report_date = st.date_input("Document/report date", value=date.today())
            source_url = st.text_input("Original HTTPS source (recommended)")
            upload = st.file_uploader("PDF or UTF-8 text", type=["pdf", "txt"])
            submitted = st.form_submit_button("Store document", type="primary")
        if submitted and title.strip() and upload is not None:
            result = api_post("/documents", data={"instrument_id": labels[stock],
                "document_type": kind, "title": title, "report_date": report_date.isoformat(),
                "source_url": source_url}, files={"file": (upload.name, upload.getvalue(), upload.type)})
            if result:
                st.success(f"Stored {result['section_count']} cited sections."); st.rerun()
        elif submitted:
            st.error("Title and file are required.")

if not documents:
    st.info("No research documents are stored yet."); st.stop()
st.dataframe(pd.DataFrame([{"Company": item["company_name"], "Stock": item["stock"],
    "Title": item["title"], "Type": item["document_type"].replace("_", " ").title(),
    "Report date": item["report_date"], "Sections": item["section_count"],
    "Analysis": item["analysis_status"], "Source": item["source_url"]} for item in documents]),
    width="stretch", hide_index=True, column_config={"Source": st.column_config.LinkColumn("Source")})
options = {f"{item['stock']} — {item['title']} ({item['report_date']})": item for item in documents}
selected = options[st.selectbox("Document to review", list(options))]
left, right = st.columns(2)
if left.button("Generate grounded draft", type="primary"):
    if api_post(f"/documents/{selected['id']}/analyze", json={"force": False}, timeout=300): st.rerun()
if right.button("Regenerate draft"):
    if api_post(f"/documents/{selected['id']}/analyze", json={"force": True}, timeout=300): st.rerun()

if selected["latest_analysis_id"]:
    analysis = api_get(f"/documents/{selected['id']}/analysis")
    citations = analysis["citations"]
    if analysis["omitted_section_count"]:
        st.warning(f"The configured model context omitted {analysis['omitted_section_count']} later section(s).")
    def labels_for(ids):
        return " ".join(f"[Section {citations[str(i)]['section_index']}]" for i in ids)
    st.subheader("Summary"); st.write(analysis["summary"]["text"])
    st.caption(labels_for(analysis["summary"]["section_ids"]))
    for key, label in (("catalysts", "Catalysts"), ("risks", "Risks"),
                       ("invalidation_conditions", "Invalidation conditions")):
        st.subheader(label)
        if not analysis[key]: st.info("No supported items extracted.")
        for claim in analysis[key]:
            st.markdown(f"- {claim['text']}  \n  {labels_for(claim['section_ids'])}")
    with st.expander("Citation evidence", expanded=True):
        for citation in sorted(citations.values(), key=lambda item: item["section_index"]):
            location = f"page {citation['page_number']}" if citation["page_number"] else citation["heading"]
            st.markdown(f"**Section {citation['section_index']} · {location}**"); st.write(citation["excerpt"])
    st.caption(f"{analysis['provider']} · {analysis['model']} · {analysis['prompt_version']} · {analysis['status']}")
    note = st.text_input("Review note")
    accept, reject = st.columns(2)
    if accept.button("Accept draft") and api_post(f"/document-analyses/{analysis['id']}/review",
            json={"decision": "ACCEPTED", "note": note or None}): st.rerun()
    if reject.button("Reject draft") and api_post(f"/document-analyses/{analysis['id']}/review",
            json={"decision": "REJECTED", "note": note or None}): st.rerun()
st.warning("Drafts are research aids, not recommendations. Verify every citation in the original document.")
