import os

import requests
import streamlit as st


API = os.getenv("API_BASE_URL", "http://localhost:8000")
# Streamlit talks to the API server-side over the Docker network (API_BASE_URL, e.g.
# http://api:8000), but a link the user's own browser navigates to (a download/view button)
# must resolve from their machine instead — the API's published host port.
PUBLIC_API = os.getenv("PUBLIC_API_BASE_URL", "http://localhost:8000")


def disclosure_document_url(document_id: int) -> str:
    return f"{PUBLIC_API}/investor-disclosures/documents/{document_id}/content"


def _coerce_detail(detail) -> str:
    """FastAPI's `detail` is usually a string, but can also be a dict (a raised
    HTTPException with a structured payload) or a list (a 422 validation-error body).
    Render either as readable text instead of a raw Python repr."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict) and "msg" in item:
                loc = ".".join(str(part) for part in item.get("loc", []) if part != "body")
                parts.append(f"{loc}: {item['msg']}" if loc else str(item["msg"]))
            else:
                parts.append(str(item))
        return "; ".join(parts) if parts else str(detail)
    if isinstance(detail, dict):
        message = detail.get("message")
        if message:
            extra = {k: v for k, v in detail.items() if k != "message"}
            return f"{message} ({extra})" if extra else str(message)
        return "; ".join(f"{k}: {v}" for k, v in detail.items())
    return str(detail)


def _response_error_message(exc: requests.RequestException) -> tuple[str, bool]:
    """Return (message, is_connection_failure). A connection failure (service down,
    timeout, DNS) has no response object; an HTTP error response (404/422/409/500) does —
    these are different problems and must not share one "service unavailable" message."""
    response = getattr(exc, "response", None)
    if response is None:
        return f"Could not reach the portfolio service: {exc}", True
    detail = response.text
    try:
        detail = _coerce_detail(response.json().get("detail", detail))
    except ValueError:
        pass
    return f"Request failed ({response.status_code}): {detail}", False


def api_get(path: str, critical: bool = True):
    """Fetch JSON from the API. Set critical=False for a call whose failure shouldn't take
    down the whole page (e.g. a secondary widget) — it then shows a warning and returns None
    instead of stopping the script, so the caller must handle a None result."""
    try:
        response = requests.get(f"{API}{path}", timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        message, is_connection_failure = _response_error_message(exc)
        if not critical:
            st.warning(message)
            return None
        st.error(message)
        if is_connection_failure:
            st.info("Check that the Docker services are running, then refresh this page.")
        st.stop()


def api_post(path: str, json=None, files=None, data=None, timeout=120):
    try:
        response = requests.post(f"{API}{path}", json=json, files=files, data=data, timeout=timeout)
        if not response.ok:
            detail = response.text
            try:
                detail = _coerce_detail(response.json().get("detail", detail))
            except ValueError:
                pass
            st.error(f"Could not complete the request: {detail}")
            return None
        return response.json()
    except requests.RequestException as exc:
        st.error(f"Could not reach the portfolio service: {exc}")
        return None


def api_put(path: str, json=None, timeout=120):
    try:
        response = requests.put(f"{API}{path}", json=json, timeout=timeout)
        if not response.ok:
            detail = response.text
            try: detail = _coerce_detail(response.json().get("detail", detail))
            except ValueError: pass
            st.error(f"Could not complete the request: {detail}")
            return None
        return response.json()
    except requests.RequestException as exc:
        st.error(f"Could not reach the portfolio service: {exc}")
        return None


def api_patch(path: str, json=None, timeout=120):
    try:
        response = requests.patch(f"{API}{path}", json=json, timeout=timeout)
        if not response.ok:
            detail = response.text
            try: detail = _coerce_detail(response.json().get("detail", detail))
            except ValueError: pass
            st.error(f"Could not complete the request: {detail}"); return None
        return response.json()
    except requests.RequestException as exc:
        st.error(f"Could not reach the portfolio service: {exc}"); return None


def instrument_label(item):
    return f"{item['exchange']}:{item['symbol']} — {item['company_name']}"


def render_sidebar():
    with st.sidebar:
        st.caption("WORKFLOW")
        st.markdown(
            "1. **Portfolio Setup** — accounts and opening data\n"
            "2. **Transactions** — buys, sells and income\n"
            "3. **Prices** — valuations and Upstox sync\n"
            "4. **Thesis & Review** — research decisions\n"
            "5. **Reconcile** — compare with your broker\n"
            "6. **Financial Analysis** — owned and Strong Buy prospects\n"
            "7. **Investor Styles** — NIFTY 500 screen, rules, and backtests\n"
            "8. **Followed Investors** — public-disclosure signals\n"
            "9. **Document Analysis** — cited reports and transcripts"
            "\n10. **Notional Portfolio** — simulated trades and performance"
            "\n11. **IPOs** — pre-IPO evidence and first-year monitoring"
            "\n12. **System Status** — automation schedule, metrics, and run history"
        )
        st.divider()
        st.caption("Read-only decision support. No orders are placed.")
