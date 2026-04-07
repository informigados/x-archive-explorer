import time
from typing import Any

import requests


class XApiClient:
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        bearer_token: str,
        base_url: str,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 10.0,
        adaptive_rate_limit_enabled: bool = True,
        rate_limit_max_wait_seconds: float = 60.0,
        time_fn=None,
        sleep_fn=None,
    ):
        if not bearer_token:
            raise ValueError("Bearer token da API X não configurado.")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.05, float(backoff_seconds))
        self.max_backoff_seconds = max(self.backoff_seconds, float(max_backoff_seconds))
        self.adaptive_rate_limit_enabled = bool(adaptive_rate_limit_enabled)
        self.rate_limit_max_wait_seconds = max(0.1, float(rate_limit_max_wait_seconds))
        self._time_fn = time_fn or time.time
        self._sleep_fn = sleep_fn or time.sleep
        self._endpoint_next_allowed_at: dict[str, float] = {}
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {bearer_token}",
                "Accept": "application/json",
            }
        )

    def resolve_user(self, username: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        if user_id:
            data = self._get(
                f"/users/{user_id}",
                params={"user.fields": "id,name,username"},
                rate_limit_key="/users/:id",
            )
            return data.get("data") or {}
        if username:
            data = self._get(
                f"/users/by/username/{username}",
                params={"user.fields": "id,name,username"},
                rate_limit_key="/users/by/username/:username",
            )
            return data.get("data") or {}
        raise ValueError("Informe username ou user_id para sincronização.")

    def get_user_tweets(
        self,
        user_id: str,
        max_results: int = 100,
        pagination_token: str | None = None,
        since_id: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "max_results": max(5, min(100, int(max_results))),
            "tweet.fields": (
                "id,text,created_at,lang,source,conversation_id,entities,attachments,"
                "in_reply_to_user_id,author_id,referenced_tweets"
            ),
            "expansions": "attachments.media_keys,author_id,in_reply_to_user_id",
            "media.fields": "media_key,type,url,preview_image_url",
            "user.fields": "id,name,username",
            "exclude": "retweets",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        if since_id:
            params["since_id"] = since_id
        return self._get(
            f"/users/{user_id}/tweets",
            params=params,
            rate_limit_key="/users/:id/tweets",
        )

    def search_recent_tweets(
        self,
        query: str,
        max_results: int = 100,
        pagination_token: str | None = None,
        start_time: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "query": query,
            "max_results": max(10, min(100, int(max_results))),
            "tweet.fields": (
                "id,text,created_at,lang,source,conversation_id,entities,attachments,"
                "in_reply_to_user_id,author_id,referenced_tweets"
            ),
            "expansions": "attachments.media_keys,author_id,in_reply_to_user_id",
            "media.fields": "media_key,type,url,preview_image_url",
            "user.fields": "id,name,username",
        }
        if pagination_token:
            params["next_token"] = pagination_token
        if start_time:
            params["start_time"] = start_time
        return self._get(
            "/tweets/search/recent",
            params=params,
            rate_limit_key="/tweets/search/recent",
        )

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        rate_limit_key: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        endpoint_key = rate_limit_key or path
        last_error = "Falha desconhecida na API X."
        for attempt in range(self.max_retries + 1):
            self._wait_for_endpoint_window(endpoint_key)
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                last_error = f"Erro de rede na API X: {exc}"
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt, retry_after=None)
                    continue
                raise RuntimeError(last_error) from exc

            if response.status_code < 400:
                self._apply_rate_limit_headers(endpoint_key, response.headers, response.status_code)
                try:
                    return response.json()
                except ValueError as exc:
                    raise RuntimeError("Resposta inválida da API X (JSON esperado).") from exc

            self._apply_rate_limit_headers(endpoint_key, response.headers, response.status_code)
            try:
                payload = response.json()
            except ValueError:
                payload = {"detail": response.text}
            request_id = response.headers.get("x-request-id") or response.headers.get("x-response-time") or "-"
            last_error = f"API X erro HTTP {response.status_code} (request_id={request_id}): {payload}"

            if response.status_code in self.RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                self._sleep_backoff(attempt, retry_after=retry_after)
                continue
            raise RuntimeError(last_error)

        raise RuntimeError(last_error)

    def _sleep_backoff(self, attempt: int, retry_after: str | None = None) -> None:
        delay = min(self.max_backoff_seconds, self.backoff_seconds * (2**attempt))
        if retry_after:
            retry_delay = None
            try:
                retry_delay = float(retry_after)
            except (TypeError, ValueError):
                retry_delay = None
            if retry_delay and retry_delay > 0:
                delay = min(self.max_backoff_seconds, retry_delay)
        self._sleep_fn(delay)

    def _wait_for_endpoint_window(self, endpoint_key: str) -> None:
        if not self.adaptive_rate_limit_enabled:
            return
        now = self._time_fn()
        next_allowed_at = self._endpoint_next_allowed_at.get(endpoint_key)
        if next_allowed_at is None:
            return
        if next_allowed_at <= now:
            self._endpoint_next_allowed_at.pop(endpoint_key, None)
            return
        wait_seconds = min(next_allowed_at - now, self.rate_limit_max_wait_seconds)
        if wait_seconds > 0:
            self._sleep_fn(wait_seconds)
        if self._time_fn() >= next_allowed_at:
            self._endpoint_next_allowed_at.pop(endpoint_key, None)

    def _apply_rate_limit_headers(
        self,
        endpoint_key: str,
        headers: dict[str, Any],
        status_code: int,
    ) -> None:
        if not self.adaptive_rate_limit_enabled:
            return
        retry_after = self._parse_retry_after(headers.get("Retry-After"))
        reset_after = self._parse_reset_after(headers.get("x-rate-limit-reset"))
        remaining = self._parse_int(headers.get("x-rate-limit-remaining"))

        wait_seconds = 0.0
        if status_code == 429:
            wait_seconds = max(retry_after or 0.0, reset_after or 0.0)
        elif remaining is not None and remaining <= 0 and reset_after is not None:
            wait_seconds = max(wait_seconds, reset_after)

        if wait_seconds > 0:
            self._set_next_allowed(endpoint_key, wait_seconds)

    def _set_next_allowed(self, endpoint_key: str, wait_seconds: float) -> None:
        bounded_wait = min(max(0.0, float(wait_seconds)), self.rate_limit_max_wait_seconds)
        if bounded_wait <= 0:
            return
        candidate = self._time_fn() + bounded_wait
        current = self._endpoint_next_allowed_at.get(endpoint_key, 0.0)
        self._endpoint_next_allowed_at[endpoint_key] = max(current, candidate)

    def _parse_retry_after(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None

    def _parse_reset_after(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            reset_ts = float(value)
        except (TypeError, ValueError):
            return None
        delta = reset_ts - self._time_fn()
        return delta if delta > 0 else None

    def _parse_int(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
