from unittest.mock import MagicMock

import requests

from common import _coerce_detail, _response_error_message


def test_coerce_detail_passes_through_plain_string():
    assert _coerce_detail("Instrument not found.") == "Instrument not found."


def test_coerce_detail_renders_dict_payload_readably_not_as_repr():
    detail = {
        "message": "Only stocks currently rated Strong Buy can enter Prospective Stocks.",
        "current_recommendation": "WATCH",
    }
    result = _coerce_detail(detail)
    assert "Only stocks currently rated Strong Buy" in result
    assert "current_recommendation" in result
    assert not result.startswith("{")


def test_coerce_detail_renders_422_validation_list_readably():
    detail = [{"loc": ["body", "quantity"], "msg": "ensure this value is greater than 0",
               "type": "value_error"}]
    result = _coerce_detail(detail)
    assert "quantity" in result
    assert "ensure this value is greater than 0" in result
    assert not result.startswith("[{")


def test_coerce_detail_falls_back_for_unusual_shapes():
    assert _coerce_detail(404) == "404"


def _http_error(status_code, json_body=None, text_body=""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text_body
    if json_body is not None:
        response.json.return_value = json_body
    else:
        response.json.side_effect = ValueError("no json body")
    return requests.HTTPError(response=response)


def test_connection_failure_and_http_error_produce_visibly_different_messages():
    connection_exc = requests.ConnectionError("Connection refused")
    connection_message, is_connection_failure = _response_error_message(connection_exc)
    assert is_connection_failure is True
    assert "could not reach" in connection_message.lower()

    http_exc = _http_error(404, {"detail": "Instrument not found."})
    http_message, is_connection_failure = _response_error_message(http_exc)
    assert is_connection_failure is False
    assert "404" in http_message
    assert "Instrument not found." in http_message

    # The two failure modes must not share the same wording — a connection failure and a
    # legitimate 404 from a running API are different problems.
    assert connection_message != http_message
    assert "could not reach" not in http_message.lower()


def test_http_error_with_dict_detail_is_coerced_not_repr():
    http_exc = _http_error(409, {"detail": {"message": "Duplicate mapping", "field": "source_code"}})
    message, is_connection_failure = _response_error_message(http_exc)
    assert is_connection_failure is False
    assert "Duplicate mapping" in message
    assert "{'message'" not in message


def test_http_error_without_json_body_falls_back_to_raw_text():
    http_exc = _http_error(500, json_body=None, text_body="Internal Server Error")
    message, is_connection_failure = _response_error_message(http_exc)
    assert is_connection_failure is False
    assert "500" in message
    assert "Internal Server Error" in message
