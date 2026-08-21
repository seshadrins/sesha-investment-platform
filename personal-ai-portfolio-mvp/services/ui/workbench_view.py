import pandas as pd
import streamlit as st

TABLE_HEIGHT = 650


def stock_name(row):
    return f"{row['exchange']}:{row['symbol']}"


def render_table(frame, column_config=None):
    if frame.empty:
        st.info("No rows are available for this view.")
        return
    st.dataframe(
        frame, width="stretch", height=TABLE_HEIGHT, hide_index=True,
        column_config=column_config or {},
    )
    st.caption(f"Showing {len(frame)} stocks. Scroll inside the table to see additional rows.")


def render_scope(workbench, rows, scope, account_positions=None):
    if not rows:
        if scope == "OWNED":
            st.info("No owned stocks were found. Use Portfolio Setup to add opening holdings.")
        else:
            st.info(
                "No prospective stock currently passes the Strong Buy gate. "
                "Continue the NIFTY 500 screen in Styles & Screening."
            )
            st.page_link("pages/5_Investor_Styles.py", label="Open NIFTY 500 screening")
        return

    st.caption(
        "The same stock rows appear in every tab (Owned is ordered by recommendation "
        "urgency — STRONG_SELL first; Prospective is alphabetical since every row there "
        "is already Strong Buy); no stock selection is required."
    )
    portfolio_tab, data_tab, financial_tab, style_tab, investor_tab, summary_tab = st.tabs([
        "Portfolio & Thesis", "Data & Freshness", "Financial Analysis",
        "Investor Style Fit", "Followed Investors", "Summary & Recommendation",
    ])

    with portfolio_tab:
        if scope == "OWNED":
            def portfolio_field(row, field):
                if account_positions is not None:
                    return account_positions.get(row["instrument_id"], {}).get(field)
                return row["portfolio"][field]

            frame = pd.DataFrame([{
                "Stock": stock_name(row),
                "Company": row["company_name"],
                "Accounts": ", ".join(row["portfolio"]["accounts"])
                            if account_positions is None
                            else account_positions.get(row["instrument_id"], {}).get("account_name", ""),
                "Quantity": portfolio_field(row, "quantity"),
                "Average cost": portfolio_field(row, "average_cost"),
                "Value at cost": portfolio_field(row, "remaining_cost"),
                "Current price": portfolio_field(row, "current_price"),
                "Market value": portfolio_field(row, "market_value"),
                "Unrealised P&L": portfolio_field(row, "unrealised_profit"),
                "Return": (portfolio_field(row, "return_pct") or 0) * 100
                          if portfolio_field(row, "return_pct") is not None else None,
                "Weight": (portfolio_field(row, "weight") or 0) * 100
                          if portfolio_field(row, "weight") is not None else None,
            } for row in rows])
            column_config = {
                "Quantity": st.column_config.NumberColumn(format="%.4f"),
                "Average cost": st.column_config.NumberColumn(format="₹%.2f"),
                "Value at cost": st.column_config.NumberColumn(format="₹%.2f"),
                "Current price": st.column_config.NumberColumn(format="₹%.2f"),
                "Market value": st.column_config.NumberColumn(format="₹%.2f"),
                "Unrealised P&L": st.column_config.NumberColumn(format="₹%.2f"),
                "Return": st.column_config.NumberColumn(format="%.2f%%"),
                "Weight": st.column_config.NumberColumn(format="%.2f%%"),
            }
        else:
            frame = pd.DataFrame([{
                "Stock": stock_name(row),
                "Company": row["company_name"],
                "Sector": row["sector"] or "Not classified",
                "Shortlisted on": row["portfolio"]["shortlisted_on"],
                "Latest price": row["portfolio"]["current_price"],
                "Price date": row["portfolio"]["price_date"],
            } for row in rows])
            column_config = {"Latest price": st.column_config.NumberColumn(format="₹%.2f")}

        if frame.empty:
            st.info("No rows are available for this view.")
        else:
            event = st.dataframe(
                frame, width="stretch", height=TABLE_HEIGHT, hide_index=True,
                column_config=column_config, on_select="rerun",
                selection_mode="single-row", key=f"portfolio_select_{scope}",
            )
            st.caption(
                f"Showing {len(frame)} stocks. Scroll inside the table to see additional rows. "
                "Select a row, then open its thesis to see or update the reasoning behind it."
            )
            selected_indices = event.selection.rows if event and event.selection else []
            if selected_indices:
                selected_row = rows[selected_indices[0]]
                if st.button("Review thesis →", key=f"jump_thesis_{scope}"):
                    st.session_state["deep_link_symbol"] = stock_name(selected_row)
                    st.switch_page("pages/3_Thesis_and_Review.py")

    with data_tab:
        frame = pd.DataFrame([{
            "Stock": stock_name(row),
            "Company": row["company_name"],
            "Stored datasets": row["data"]["dataset_count"],
            "Latest evidence": row["data"]["latest_evidence"],
            "Providers": ", ".join(row["data"]["providers"]) or "None",
            "Price": row["portfolio"]["current_price"],
            "Price date": row["portfolio"]["price_date"],
            "Provider errors": len(row["data"]["provider_errors"]),
            "Available evidence": ", ".join(
                item.replace("_", " ").title() for item in row["data"]["datasets"]
            ) or "None",
        } for row in rows])
        render_table(frame, {"Price": st.column_config.NumberColumn(format="₹%.2f")})

    with financial_tab:
        frame = pd.DataFrame([{
            "Stock": stock_name(row),
            "Company": row["company_name"],
            "Sector": row["sector"] or "Not classified",
            "Status": row["financials"]["status"].replace("_", " "),
            "As of": row["financials"]["as_of"],
            "Overall score": row["financials"]["overall_score"],
            "ROCE": row["financials"]["scores"].get("roce"),
            "ROE": row["financials"]["scores"].get("roe"),
            "Leverage": row["financials"]["scores"].get("leverage"),
            "Cash conversion": row["financials"]["scores"].get("cash_conversion"),
            "Governance flags": len(row["financials"]["governance_flags"]),
            "Missing evidence": ", ".join(
                item.replace("_", " ").title() for item in row["financials"]["missing"]
            ),
        } for row in rows])
        render_table(frame)

    with style_tab:
        style_rows = []
        for row in rows:
            item = {"Stock": stock_name(row), "Company": row["company_name"]}
            results = {result["style_id"]: result for result in row["styles"]}
            for definition in workbench["style_definitions"]:
                result = results.get(definition["id"])
                if not result:
                    value = "NO DATA"
                elif not result["applicable"]:
                    value = "NOT APPLICABLE"
                else:
                    state = "MATCH" if result["matches"] else "NO MATCH"
                    value = f"{state} · {result['score']:.0f}/100 · {result['coverage']:.0f}% evidence"
                item[f"{definition['name']} v{definition['version']}"] = value
            style_rows.append(item)
        render_table(pd.DataFrame(style_rows))

    with investor_tab:
        investor_rows = []
        for row in rows:
            item = {"Stock": stock_name(row), "Company": row["company_name"]}
            activity = {signal["investor_id"]: signal for signal in row["investors"]}
            for profile in workbench["investor_profiles"]:
                signal = activity.get(profile["id"])
                if not signal:
                    value = "—"
                else:
                    stale = " · STALE" if signal["stale"] else ""
                    value = (
                        f"{signal['signal'].replace('_', ' ')} · "
                        f"{signal['ownership_pct']:.2f}% · {signal['report_date']}{stale}"
                    )
                item[profile["name"]] = value
            investor_rows.append(item)
        render_table(pd.DataFrame(investor_rows))
        st.caption(
            "A blank cell means no attributable disclosure is stored. Investor activity is "
            "corroborating evidence and does not change the recommendation."
        )

    with summary_tab:
        frame = pd.DataFrame([{
            "Stock": stock_name(row),
            "Company": row["company_name"],
            "Recommendation": row["summary"]["recommendation"].replace("_", " "),
            "Brief rationale": row["summary"]["brief_reason"],
            "Financial score": row["financials"]["overall_score"],
            "Style matches": len(row["summary"]["style_matches"]),
            "Matched styles": ", ".join(row["summary"]["style_matches"]) or "None",
            "Governance flags": len(row["financials"]["governance_flags"]),
            "Investor signals": len(row["investors"]),
            "Financial evidence": row["financials"]["as_of"],
            "Price date": row["portfolio"]["price_date"],
        } for row in rows])
        if frame.empty:
            st.info("No rows are available for this view.")
        else:
            event = st.dataframe(
                frame, width="stretch", height=TABLE_HEIGHT, hide_index=True,
                on_select="rerun", selection_mode="single-row", key=f"summary_select_{scope}",
            )
            st.caption(
                f"Showing {len(frame)} stocks. Select a row, then jump to its Governance "
                "flags or Style Fit detail with that stock already selected."
            )
            selected_indices = event.selection.rows if event and event.selection else []
            if selected_indices:
                selected_row = rows[selected_indices[0]]
                jump_cols = st.columns([1, 1, 4])
                if jump_cols[0].button("Open Financial Analysis", key=f"jump_financial_{scope}"):
                    st.session_state["deep_link_symbol"] = stock_name(selected_row)
                    st.switch_page("pages/4_Financial_Analysis.py")
                if jump_cols[1].button("Open Investor Style Fit", key=f"jump_styles_{scope}"):
                    st.session_state["deep_link_symbol"] = stock_name(selected_row)
                    st.switch_page("pages/5_Investor_Styles.py")
        st.download_button(
            f"Download {scope.lower()} summary",
            frame.to_csv(index=False).encode("utf-8"),
            file_name=f"{scope.lower()}_stock_summary.csv",
            mime="text/csv",
        )
        st.warning("Decision support only. Verify source data, suitability, valuation, and risk before acting.")
