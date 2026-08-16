import pandas as pd
import streamlit as st

from common import api_post

st.title("Portfolio Reconciliation")
st.write(
    "Upload a broker holding summary using "
    "`imports/reconciliation_template.csv`. The application compares it "
    "with holdings calculated from the transaction ledger."
)

upload = st.file_uploader("Broker holdings CSV", type=["csv"])
if st.button("Run reconciliation", disabled=upload is None):
    result = api_post(
        "/reconcile",
        files={"file": (upload.name, upload.getvalue(), "text/csv")},
    )
    if result:
        df = pd.DataFrame(result["results"])
        st.dataframe(df, use_container_width=True, hide_index=True)
        differences = df[df["status"] != "MATCH"]
        if differences.empty:
            st.success("All broker quantities match the calculated portfolio.")
        else:
            st.warning(f"{len(differences)} difference(s) require review.")
