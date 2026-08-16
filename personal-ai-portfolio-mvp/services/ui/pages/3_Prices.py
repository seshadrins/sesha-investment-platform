from datetime import date

import pandas as pd
import streamlit as st

from common import api_get, api_post, instrument_label, render_sidebar

render_sidebar()
st.title("Prices & Research Data")
st.caption("Keep valuations current automatically with Upstox Analytics or use manual/CSV fallbacks.")

instruments = api_get("/instruments")
upstox = api_get("/providers/upstox")
prices = api_get("/prices")

sync_tab, manual_tab, csv_tab, history_tab = st.tabs(["Upstox sync", "Manual price", "CSV imports", "Price history"])

with sync_tab:
    st.subheader("Upstox Analytics (read-only)")
    if upstox["configured"]:
        c1, c2, c3 = st.columns(3)
        c1.metric("Connection", "Configured")
        c2.metric("Eligible instruments", upstox["eligible_instruments"])
        c3.metric("Needs attention", len(upstox["skipped_instruments"]))
        st.caption("Sync uses daily historical candles—not intraday prices—so valuations remain reproducible.")
        if upstox["skipped_instruments"]:
            st.warning("Add a valid ISIN and use NSE/BSE for: " + ", ".join(upstox["skipped_instruments"]))
        sync_date = st.date_input("Valuation date", value=date.today(), key="upstox_date",
                                  help="On weekends/holidays, the latest prior trading-day close is used.")
        include_profiles = st.checkbox("Also refresh company profiles and sectors", value=True)
        if st.button("Sync from Upstox", type="primary", disabled=upstox["eligible_instruments"] == 0):
            with st.spinner("Fetching read-only data from Upstox…"):
                result = api_post("/providers/upstox/sync", json={"as_of": sync_date.isoformat(),
                    "include_prices": True, "include_company_profiles": include_profiles})
            if result:
                st.success(f"Imported {result['prices_imported']} prices and {result['profiles_imported']} company profiles.")
                if result["provider_errors"]:
                    st.warning(f"Upstox could not return some data ({len(result['provider_errors'])} items).")
                    with st.expander("Show instruments needing attention"):
                        for error in result["provider_errors"]:
                            st.write(f"• {error}")
    else:
        st.info("Set `UPSTOX_ANALYTICS_TOKEN` in `.env`, then restart Docker Compose.")
        st.code("UPSTOX_ANALYTICS_TOKEN=your_full_analytics_token", language="dotenv")
    st.caption("The integration only performs GET requests. It cannot place, modify, or cancel orders.")

with manual_tab:
    if not instruments:
        st.info("Add instruments under Transactions first.")
    else:
        instrument_map = {instrument_label(i): i["id"] for i in instruments}
        with st.form("price_form"):
            selected = st.selectbox("Instrument", instrument_map)
            c1, c2 = st.columns(2)
            price_date = c1.date_input("Price date", value=date.today())
            close_price = c2.number_input("Closing price", min_value=0.01, step=1.0)
            if st.form_submit_button("Save price", type="primary"):
                result = api_post("/prices", json={"instrument_id": instrument_map[selected],
                    "price_date": price_date.isoformat(), "close_price": close_price})
                if result:
                    st.success("Price saved.")

with csv_tab:
    st.subheader("Price import")
    st.download_button("Download price template", data=(
        "exchange,symbol,price_date,price\nNSE,INFY,2026-08-01,1520.00\n"
    ), file_name="prices_template.csv", mime="text/csv")
    upload = st.file_uploader("Choose prices CSV", type=["csv"])
    if st.button("Import prices", disabled=upload is None):
        result = api_post("/imports/prices", files={"file": (upload.name, upload.getvalue(), "text/csv")})
        if result:
            st.success(f"Imported {result['imported']} prices.")

    st.divider()
    st.subheader("Company metadata import")
    st.write("Use this fallback to update name, ISIN, sector, and industry without Upstox.")
    st.download_button("Download company metadata template", data=(
        "exchange,symbol,company_name,isin,sector,industry\n"
        "NSE,INFY,Infosys Limited,INE009A01021,Information Technology,IT Services\n"
    ), file_name="company_research_template.csv", mime="text/csv")
    research_upload = st.file_uploader("Choose company metadata CSV", type=["csv"], key="research_csv")
    if st.button("Import company metadata", disabled=research_upload is None):
        result = api_post("/imports/company-research", files={"file": (
            research_upload.name, research_upload.getvalue(), "text/csv")})
        if result:
            st.success(f"Updated {result['imported']} instruments.")

with history_tab:
    if not prices:
        st.info("No prices stored yet.")
    else:
        latest_only = st.checkbox("Show latest price per instrument", value=True)
        df = pd.DataFrame(prices)
        if latest_only:
            df = df.drop_duplicates(subset=["instrument_id"], keep="first")
        df = df[["exchange", "symbol", "company_name", "price_date", "close_price", "source"]]
        df.columns = ["Exchange", "Symbol", "Company", "Price date", "Close", "Source"]
        st.dataframe(df, width="stretch", hide_index=True,
                     column_config={"Close": st.column_config.NumberColumn(format="₹%.2f")})
