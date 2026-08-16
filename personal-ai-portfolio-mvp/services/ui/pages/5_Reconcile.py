import pandas as pd
import streamlit as st

from common import api_post, render_sidebar

render_sidebar()
st.title("Portfolio Reconciliation")
st.caption("Compare broker-reported quantities with holdings independently calculated from your ledger.")

st.download_button("Download reconciliation template", data=(
    "account_name,exchange,symbol,broker_quantity\nPrimary,NSE,INFY,5\n"
), file_name="reconciliation_template.csv", mime="text/csv")

with st.expander("How to reconcile safely"):
    st.markdown("Export current holdings from your broker, map them to the four template columns, and upload the file. This check does not modify your ledger. Resolve differences using documented adjustment transactions—never by silently editing balances.")

upload = st.file_uploader("Choose broker holdings CSV", type=["csv"])
if st.button("Run reconciliation", type="primary", disabled=upload is None):
    result = api_post("/reconcile", files={"file": (upload.name, upload.getvalue(), "text/csv")})
    if result:
        df = pd.DataFrame(result["results"])
        matches = int((df["status"] == "MATCH").sum()) if not df.empty else 0
        differences = df[df["status"] != "MATCH"] if not df.empty else df
        c1, c2, c3 = st.columns(3)
        c1.metric("Compared", len(df))
        c2.metric("Matches", matches)
        c3.metric("Needs review", len(differences))
        st.dataframe(df.rename(columns={"account_name": "Account", "exchange": "Exchange",
            "symbol": "Symbol", "calculated_quantity": "Ledger quantity",
            "broker_quantity": "Broker quantity", "difference": "Difference", "status": "Status"}),
            width="stretch", hide_index=True)
        if differences.empty:
            st.success("All broker quantities match the calculated portfolio.")
        else:
            st.warning("Review every difference before recording an adjustment transaction.")
