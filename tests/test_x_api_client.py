import requests

from app.services.x_api_client import XApiClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_x_api_client_retries_429_then_success(monkeypatch):
    client = XApiClient(
        bearer_token="token",
        base_url="https://api.example.com/2",
        max_retries=2,
        backoff_seconds=0.01,
        max_backoff_seconds=0.02,
    )
    calls = {"count": 0}

    responses = [
        FakeResponse(status_code=429, payload={"title": "rate limit"}, headers={"Retry-After": "0"}),
        FakeResponse(status_code=200, payload={"data": {"id": "1"}}),
    ]

    def fake_get(url, params=None, timeout=None):
        idx = calls["count"]
        calls["count"] += 1
        return responses[idx]

    monkeypatch.setattr(client.session, "get", fake_get)
    result = client.resolve_user(user_id="1")
    assert result["id"] == "1"
    assert calls["count"] == 2


def test_x_api_client_raises_on_non_retryable_error(monkeypatch):
    client = XApiClient(
        bearer_token="token",
        base_url="https://api.example.com/2",
        max_retries=2,
        backoff_seconds=0.01,
        max_backoff_seconds=0.02,
    )

    def fake_get(url, params=None, timeout=None):
        return FakeResponse(status_code=401, payload={"title": "unauthorized"}, headers={"x-request-id": "abc"})

    monkeypatch.setattr(client.session, "get", fake_get)
    try:
        client.resolve_user(user_id="1")
        assert False, "Era esperado RuntimeError"
    except RuntimeError as exc:
        assert "401" in str(exc)
        assert "request_id=abc" in str(exc)


def test_x_api_client_retries_request_exception(monkeypatch):
    client = XApiClient(
        bearer_token="token",
        base_url="https://api.example.com/2",
        max_retries=1,
        backoff_seconds=0.01,
        max_backoff_seconds=0.02,
    )
    calls = {"count": 0}

    def fake_get(url, params=None, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.ConnectionError("network down")
        return FakeResponse(status_code=200, payload={"data": {"id": "1", "username": "demo"}})

    monkeypatch.setattr(client.session, "get", fake_get)
    result = client.resolve_user(user_id="1")
    assert result["id"] == "1"
    assert calls["count"] == 2


def test_x_api_client_applies_rate_limit_window_for_same_endpoint(monkeypatch):
    timeline = {"now": 1000.0}
    sleep_calls = []

    def fake_time():
        return timeline["now"]

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        timeline["now"] += seconds

    client = XApiClient(
        bearer_token="token",
        base_url="https://api.example.com/2",
        max_retries=0,
        adaptive_rate_limit_enabled=True,
        rate_limit_max_wait_seconds=10.0,
        time_fn=fake_time,
        sleep_fn=fake_sleep,
    )
    calls = {"count": 0}

    responses = [
        FakeResponse(
            status_code=200,
            payload={"data": []},
            headers={"x-rate-limit-remaining": "0", "x-rate-limit-reset": "1005"},
        ),
        FakeResponse(status_code=200, payload={"data": []}),
    ]

    def fake_get(url, params=None, timeout=None):
        idx = calls["count"]
        calls["count"] += 1
        return responses[idx]

    monkeypatch.setattr(client.session, "get", fake_get)
    client.get_user_tweets(user_id="u1")
    client.get_user_tweets(user_id="u1")

    assert calls["count"] == 2
    assert any(delay >= 5.0 for delay in sleep_calls)


def test_x_api_client_rate_limit_isolation_between_endpoints(monkeypatch):
    timeline = {"now": 2000.0}
    sleep_calls = []

    def fake_time():
        return timeline["now"]

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        timeline["now"] += seconds

    client = XApiClient(
        bearer_token="token",
        base_url="https://api.example.com/2",
        max_retries=0,
        adaptive_rate_limit_enabled=True,
        rate_limit_max_wait_seconds=10.0,
        time_fn=fake_time,
        sleep_fn=fake_sleep,
    )

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/tweets"):
            return FakeResponse(
                status_code=200,
                payload={"data": []},
                headers={"x-rate-limit-remaining": "0", "x-rate-limit-reset": "2008"},
            )
        return FakeResponse(status_code=200, payload={"data": {"id": "1"}})

    monkeypatch.setattr(client.session, "get", fake_get)
    client.get_user_tweets(user_id="u1")
    client.resolve_user(user_id="1")

    assert sleep_calls == []
