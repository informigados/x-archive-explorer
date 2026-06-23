import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

import app as app_module
from app.config import TestConfig
from app.extensions import db
from app.models import Archive, ImportJob, OperationMetric, Post, User
from app.services.import_queue import process_next_queued_job, requeue_stalled_jobs
from app.services.search import ensure_fts_table


@pytest.fixture()
def app():
    upload_dir = tempfile.mkdtemp(prefix="xae-test-uploads-")
    os.environ["XAE_ADMIN_EMAIL"] = "admin@example.com"
    os.environ["XAE_ADMIN_PASSWORD"] = "TestPass123!"
    os.environ["XAE_UPLOAD_FOLDER"] = upload_dir

    flask_app = app_module.create_app(TestConfig)
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        ensure_fts_table()
        app_module.ensure_admin_user(flask_app)

    yield flask_app
    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client):
    return client.post(
        "/auth/login",
        data={"email": "admin@example.com", "password": "TestPass123!"},
        follow_redirects=True,
    )


def _create_archive(client, name="Arquivo Teste"):
    return client.post(
        "/archives/create",
        data={"name": name, "description": "Teste"},
        follow_redirects=True,
    )


def _import_json(client, archive_id: int, payload, mode: str = "merge"):
    file_bytes = io.BytesIO(json.dumps(payload).encode("utf-8"))
    return client.post(
        f"/archives/{archive_id}/import",
        data={"archive_file": (file_bytes, "tweets.json"), "import_mode": mode},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _import_zip(client, archive_id: int, files: dict[str, bytes], mode: str = "merge"):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    zip_buffer.seek(0)
    return client.post(
        f"/archives/{archive_id}/import",
        data={"archive_file": (zip_buffer, "archive.zip"), "import_mode": mode},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_requires_auth(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert "/auth/login" in response.headers["Location"]


def test_language_switch_to_english(client):
    response = client.post("/language", data={"lang": "en", "next": "/auth/login"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Sign in" in response.data
    assert b"Language" in response.data


def test_create_app_requires_strong_secret_key_outside_development():
    class WeakSecretConfig(TestConfig):
        ENVIRONMENT = "production"
        SECRET_KEY = "dev-change-me"

    with pytest.raises(RuntimeError):
        app_module.create_app(WeakSecretConfig)


def test_is_flask_db_command_detection(monkeypatch):
    monkeypatch.setattr(app_module.sys, "argv", ["flask", "--app", "run.py", "db", "upgrade"])
    assert app_module._is_flask_db_command() is True

    monkeypatch.setattr(app_module.sys, "argv", ["flask", "run"])
    assert app_module._is_flask_db_command() is False


def test_login_with_username(client):
    response = client.post(
        "/auth/login",
        data={"email": "admin", "password": "TestPass123!"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_login_blocks_external_next_redirect(client):
    response = client.post(
        "/auth/login?next=https://evil.example/",
        data={"email": "admin@example.com", "password": "TestPass123!"},
        follow_redirects=False,
    )
    assert response.status_code in (301, 302)
    assert "/dashboard" in response.headers["Location"]


def test_login_rate_limit_blocks_after_multiple_failures(client):
    ip = "203.0.113.55"
    for _ in range(5):
        response = client.post(
            "/auth/login",
            data={"email": "admin@example.com", "password": "wrong-password"},
            environ_overrides={"REMOTE_ADDR": ip},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Credenciais inválidas".encode("utf-8") in response.data

    blocked = client.post(
        "/auth/login",
        data={"email": "admin@example.com", "password": "wrong-password"},
        environ_overrides={"REMOTE_ADDR": ip},
        follow_redirects=True,
    )
    assert blocked.status_code == 200
    assert "Muitas tentativas de login".encode("utf-8") in blocked.data


def test_login_rate_limit_uses_forwarded_for_only_when_proxy_is_trusted(client, app):
    app.config["TRUST_PROXY_HEADERS"] = False
    from app.auth.routes import _build_login_rate_limit_key

    with app.test_request_context(
        "/auth/login",
        headers={"X-Forwarded-For": "203.0.113.1"},
        environ_base={"REMOTE_ADDR": "198.51.100.99"},
    ):
        key = _build_login_rate_limit_key()
        assert key == "login-ip:198.51.100.99"

    app.config["TRUST_PROXY_HEADERS"] = True
    with app.test_request_context(
        "/auth/login",
        headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.10"},
        environ_base={"REMOTE_ADDR": "198.51.100.99"},
    ):
        key = _build_login_rate_limit_key()
        assert key == "login-ip:203.0.113.1"


def test_language_switch_blocks_external_next_redirect(client):
    response = client.post(
        "/language",
        data={"lang": "en", "next": "https://evil.example/"},
        follow_redirects=False,
    )
    assert response.status_code in (301, 302)
    assert response.headers["Location"] == "/"


def test_import_and_search_flow(client, app):
    health = client.get("/healthz")
    assert health.status_code == 200
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.get_json()["status"] == "ready"

    login_response = _login(client)
    assert login_response.status_code == 200
    assert b"Dashboard" in login_response.data

    create_response = _create_archive(client)
    assert create_response.status_code == 200

    payload = [
        {
            "tweet": {
                "id_str": "10001",
                "full_text": "Hello archive world #Flask @openai https://example.com",
                "created_at": "Tue Apr 02 10:00:00 +0000 2024",
                "lang": "en",
                "entities": {
                    "hashtags": [{"text": "Flask"}],
                    "user_mentions": [{"screen_name": "openai"}],
                    "urls": [{"url": "https://t.co/abc", "expanded_url": "https://example.com"}],
                },
            }
        }
    ]
    import_response = _import_json(client, archive_id=1, payload=payload)
    assert import_response.status_code == 200
    assert "Importação concluída".encode("utf-8") in import_response.data
    with app.app_context():
        metric = (
            OperationMetric.query.filter_by(archive_id=1, operation_type="import", status="success")
            .order_by(OperationMetric.id.desc())
            .first()
        )
        assert metric is not None
        assert metric.imported_count == 1

    search_response = client.get("/search?q=hello", follow_redirects=True)
    assert search_response.status_code == 200
    assert b"Hello archive world" in search_response.data

    export_response = client.get("/export/json?q=hello", follow_redirects=True)
    assert export_response.status_code == 200
    assert export_response.mimetype == "application/json"


def test_import_tweetclaw_json_export(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200

    create_response = _create_archive(client, name="TweetClaw Export")
    assert create_response.status_code == 200

    payload = {
        "items": [
            {
                "id": "1900000000000000000",
                "text": "TweetClaw source post #Launch @builder https://example.com/item",
                "created_at": "2026-06-23T20:00:00Z",
                "language": "en",
                "url": "https://x.com/example/status/1900000000000000000",
                "author": {
                    "username": "example_creator",
                    "name": "Example Creator",
                },
                "media": [
                    {
                        "type": "photo",
                        "url": "https://example.com/media.jpg",
                    }
                ],
            }
        ]
    }
    import_response = _import_json(client, archive_id=1, payload=payload)
    assert import_response.status_code == 200
    assert "Importação concluída".encode("utf-8") in import_response.data

    with app.app_context():
        post = Post.query.filter_by(external_post_id="1900000000000000000").first()
        assert post is not None
        assert post.author_handle == "example_creator"
        assert post.author_display_name == "Example Creator"
        assert post.language == "en"
        assert post.has_links is True
        assert post.has_media is True
        assert post.text_raw == "TweetClaw source post #Launch @builder https://example.com/item"
        assert len(post.urls) == 1
        assert post.urls[0].expanded_url == "https://x.com/example/status/1900000000000000000"
        assert len(post.media) == 1
        assert post.media[0].media_url == "https://example.com/media.jpg"


def test_import_without_raw_json_storage(client, app):
    app.config["STORE_RAW_JSON"] = False
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo Sem Raw")
    assert create_response.status_code == 200

    payload = [
        {
            "tweet": {
                "id_str": "raw-off-1",
                "full_text": "No raw payload storage",
                "created_at": "Tue Apr 02 10:00:00 +0000 2024",
                "lang": "en",
                "entities": {"hashtags": [], "user_mentions": [], "urls": []},
            }
        }
    ]
    import_response = _import_json(client, archive_id=1, payload=payload, mode="merge")
    assert import_response.status_code == 200

    with app.app_context():
        post = Post.query.filter_by(external_post_id="raw-off-1").first()
        assert post is not None
        assert post.raw_json is None


def test_reimport_merge_and_overwrite_modes(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200

    create_response = _create_archive(client, name="Arquivo Reimport")
    assert create_response.status_code == 200

    payload_v1 = [
        {
            "tweet": {
                "id_str": "20001",
                "full_text": "Original post text",
                "created_at": "Tue Apr 02 10:00:00 +0000 2024",
                "lang": "en",
                "entities": {"hashtags": [], "user_mentions": [], "urls": []},
            }
        }
    ]
    payload_v2 = [
        {
            "tweet": {
                "id_str": "20001",
                "full_text": "Updated post text",
                "created_at": "Tue Apr 02 10:00:00 +0000 2024",
                "lang": "en",
                "entities": {"hashtags": [], "user_mentions": [], "urls": []},
            }
        }
    ]

    first_import = _import_json(client, archive_id=1, payload=payload_v1, mode="merge")
    assert first_import.status_code == 200
    assert "Importação (merge) concluída".encode("utf-8") in first_import.data

    second_import_merge = _import_json(client, archive_id=1, payload=payload_v2, mode="merge")
    assert second_import_merge.status_code == 200
    assert b"Duplicados: 1" in second_import_merge.data

    with app.app_context():
        posts = Post.query.filter_by(archive_id=1).all()
        assert len(posts) == 1
        assert posts[0].text_raw == "Original post text"

    third_import_overwrite = _import_json(client, archive_id=1, payload=payload_v2, mode="overwrite")
    assert third_import_overwrite.status_code == 200
    assert "Importação (overwrite) concluída".encode("utf-8") in third_import_overwrite.data

    with app.app_context():
        posts = Post.query.filter_by(archive_id=1).all()
        assert len(posts) == 1
        assert posts[0].text_raw == "Updated post text"


def test_worker_requeues_stalled_running_jobs(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo Stalled")
    assert create_response.status_code == 200

    with app.app_context():
        archive = db.session.get(Archive, 1)
        job = ImportJob(
            archive_id=archive.id,
            status="running",
            import_mode="merge",
            original_filename="stalled.json",
            stored_path="uploads/stalled.json",
            attempt_count=1,
            max_retries=2,
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        archive.status = "processing"
        db.session.add(job)
        db.session.commit()

        recovered = requeue_stalled_jobs(stalled_minutes=30)
        assert recovered == 1

        refreshed = db.session.get(ImportJob, job.id)
        assert refreshed is not None
        assert refreshed.status == "queued"
        assert "recuperado automaticamente" in (refreshed.last_error or "")
        assert archive.status == "queued"


def test_async_queue_processing_flow(client, app):
    app.config["IMPORT_ASYNC"] = True

    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo Async")
    assert create_response.status_code == 200

    payload = [
        {
            "tweet": {
                "id_str": "30001",
                "full_text": "Async queue import works",
                "created_at": "Tue Apr 02 10:00:00 +0000 2024",
                "lang": "en",
                "entities": {"hashtags": [], "user_mentions": [], "urls": []},
            }
        }
    ]

    import_response = _import_json(client, archive_id=1, payload=payload, mode="merge")
    assert import_response.status_code == 200
    assert "Importação enfileirada".encode("utf-8") in import_response.data

    with app.app_context():
        job = ImportJob.query.filter_by(archive_id=1).first()
        assert job is not None
        assert job.status == "queued"

        processed = process_next_queued_job()
        assert processed is not None

        refreshed = db.session.get(ImportJob, job.id)
        assert refreshed.status == "completed"
        assert refreshed.imported_count == 1
        job_metric = (
            OperationMetric.query.filter_by(
                archive_id=1,
                operation_type="import_job",
                status="success",
            )
            .order_by(OperationMetric.id.desc())
            .first()
        )
        assert job_metric is not None
        assert job_metric.imported_count == 1

        posts = Post.query.filter_by(archive_id=1).all()
        assert len(posts) == 1
        assert posts[0].text_raw == "Async queue import works"


def test_media_preview_extraction_from_zip(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo Midia")
    assert create_response.status_code == 200

    tweet_payload = [
        {
            "tweet": {
                "id_str": "40001",
                "full_text": "Post com imagem local",
                "created_at": "Tue Apr 02 10:00:00 +0000 2024",
                "lang": "pt",
                "entities": {
                    "hashtags": [],
                    "user_mentions": [],
                    "urls": [],
                    "media": [
                        {
                            "type": "photo",
                            "media_url_https": "https://pbs.twimg.com/media/sample.jpg",
                        }
                    ],
                },
            }
        }
    ]
    files = {
        "data/tweet.js": (
            "window.YTD.tweet.part0 = " + json.dumps(tweet_payload, ensure_ascii=False) + ";"
        ).encode("utf-8"),
        "data/tweet_media/sample.jpg": b"\xff\xd8\xff\xdbfake-jpeg-content",
    }
    import_response = _import_zip(client, archive_id=1, files=files, mode="merge")
    assert import_response.status_code == 200
    assert "Importação concluída".encode("utf-8") in import_response.data

    with app.app_context():
        post = Post.query.filter_by(archive_id=1, external_post_id="40001").first()
        assert post is not None
        assert len(post.media) == 1
        media_item = post.media[0]
        assert media_item.local_path is not None
        stored_file = os.path.join(app.config["UPLOAD_FOLDER"], media_item.local_path.replace("/", os.sep))
        assert os.path.exists(stored_file)
        post_id = post.id
        media_path = media_item.local_path

    detail_response = client.get(f"/posts/{post_id}", follow_redirects=True)
    assert detail_response.status_code == 200
    assert b"/media/" in detail_response.data

    media_response = client.get(f"/media/{media_path}", follow_redirects=True)
    assert media_response.status_code == 200
    assert media_response.data.startswith(b"\xff\xd8\xff")

    blocked = client.get("/media/../instance/x_archive.db", follow_redirects=False)
    assert blocked.status_code == 404


def test_x_api_sync_service_imports_timeline(client, app, monkeypatch):
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo API")
    assert create_response.status_code == 200

    from app.services import x_api_sync

    class FakeXApiClient:
        def __init__(self, bearer_token, base_url, timeout_seconds, **kwargs):
            self.bearer_token = bearer_token
            self.base_url = base_url
            self.timeout_seconds = timeout_seconds

        def resolve_user(self, username=None, user_id=None):
            return {"id": "u-1", "username": username or "demo", "name": "Demo User"}

        def get_user_tweets(self, user_id, max_results=100, pagination_token=None, since_id=None):
            if not pagination_token:
                return {
                    "data": [
                        {
                            "id": "50001",
                            "text": "Timeline post #One",
                            "created_at": "2024-04-02T10:00:00Z",
                            "lang": "en",
                            "conversation_id": "50001",
                            "entities": {
                                "hashtags": [{"tag": "One"}],
                                "mentions": [{"username": "openai"}],
                                "urls": [{"url": "https://t.co/x", "expanded_url": "https://example.com/a"}],
                            },
                            "attachments": {"media_keys": ["3_1"]},
                        },
                        {
                            "id": "50002",
                            "text": "This is a reply",
                            "created_at": "2024-04-02T11:00:00Z",
                            "lang": "en",
                            "conversation_id": "50001",
                            "referenced_tweets": [{"type": "replied_to", "id": "49999"}],
                            "author_id": "u-1",
                        },
                    ],
                    "includes": {
                        "users": [{"id": "u-1", "username": "demo", "name": "Demo User"}],
                        "media": [
                            {"media_key": "3_1", "type": "photo", "url": "https://cdn.example.com/img.jpg"}
                        ]
                    },
                    "meta": {"next_token": "nxt"},
                }
            return {
                "data": [
                    {
                        "id": "50003",
                        "text": "Another timeline post",
                        "created_at": "2024-04-03T09:00:00Z",
                        "lang": "en",
                        "conversation_id": "50003",
                        "author_id": "u-1",
                    }
                ],
                "includes": {"users": [{"id": "u-1", "username": "demo", "name": "Demo User"}]},
                "meta": {},
            }

        def search_recent_tweets(self, query, max_results=100, pagination_token=None, start_time=None):
            return {"data": [], "meta": {}}

    monkeypatch.setattr(x_api_sync, "XApiClient", FakeXApiClient)

    with app.app_context():
        archive = db.session.get(Archive, 1)
        result = x_api_sync.sync_archive_from_x_api(
            archive=archive,
            username="demo",
            import_mode="merge",
            include_replies=False,
            max_pages=5,
            max_results=50,
        )
        assert result["imported_count"] == 2
        assert result["duplicate_count"] == 0
        assert result["pages_loaded"] == 2
        assert result["fetched_raw_tweets"] == 3
        posts = Post.query.filter_by(archive_id=archive.id).all()
        assert len(posts) == 2
        assert all(post.is_reply is False for post in posts)
        metric = (
            OperationMetric.query.filter_by(
                archive_id=archive.id,
                operation_type="api_sync",
                status="success",
            )
            .order_by(OperationMetric.id.desc())
            .first()
        )
        assert metric is not None
        assert metric.fetched_count == 3


def test_x_api_sync_incremental_since_id_persistence(client, app, monkeypatch):
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo API Incremental")
    assert create_response.status_code == 200

    from app.services import x_api_sync

    calls = []

    class FakeXApiClient:
        def __init__(self, bearer_token, base_url, timeout_seconds, **kwargs):
            pass

        def resolve_user(self, username=None, user_id=None):
            return {"id": "u-9", "username": "demo", "name": "Demo User"}

        def get_user_tweets(self, user_id, max_results=100, pagination_token=None, since_id=None):
            calls.append(since_id)
            if since_id is None:
                return {
                    "data": [
                        {
                            "id": "90001",
                            "text": "Primeiro sync",
                            "created_at": "2024-04-01T10:00:00Z",
                            "conversation_id": "90001",
                            "author_id": "u-9",
                        }
                    ],
                    "includes": {"users": [{"id": "u-9", "username": "demo", "name": "Demo User"}]},
                    "meta": {},
                }
            return {
                "data": [
                    {
                        "id": "90002",
                        "text": "Segundo sync incremental",
                        "created_at": "2024-04-02T10:00:00Z",
                        "conversation_id": "90002",
                        "author_id": "u-9",
                    }
                ],
                "includes": {"users": [{"id": "u-9", "username": "demo", "name": "Demo User"}]},
                "meta": {},
            }

        def search_recent_tweets(self, query, max_results=100, pagination_token=None, start_time=None):
            return {"data": [], "meta": {}}

    monkeypatch.setattr(x_api_sync, "XApiClient", FakeXApiClient)

    with app.app_context():
        archive = db.session.get(Archive, 1)
        first = x_api_sync.sync_archive_from_x_api(
            archive=archive,
            username="demo",
            incremental_sync=True,
            include_conversation_replies=False,
        )
        second = x_api_sync.sync_archive_from_x_api(
            archive=archive,
            username="demo",
            incremental_sync=True,
            include_conversation_replies=False,
        )
        assert first["since_id_used"] is None
        assert second["since_id_used"] == "90001"
        assert calls == [None, "90001"]
        assert archive.api_sync_state.since_id == "90002"
        posts = Post.query.filter_by(archive_id=archive.id).all()
        assert len(posts) == 2


def test_x_api_sync_collects_conversation_replies(client, app, monkeypatch):
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo API Replies")
    assert create_response.status_code == 200

    from app.services import x_api_sync

    class FakeXApiClient:
        def __init__(self, bearer_token, base_url, timeout_seconds, **kwargs):
            pass

        def resolve_user(self, username=None, user_id=None):
            return {"id": "u-a", "username": "owner", "name": "Owner User"}

        def get_user_tweets(self, user_id, max_results=100, pagination_token=None, since_id=None):
            return {
                "data": [
                    {
                        "id": "91001",
                        "text": "Post base",
                        "created_at": "2024-04-01T10:00:00Z",
                        "conversation_id": "91001",
                        "author_id": "u-a",
                    }
                ],
                "includes": {"users": [{"id": "u-a", "username": "owner", "name": "Owner User"}]},
                "meta": {},
            }

        def search_recent_tweets(self, query, max_results=100, pagination_token=None, start_time=None):
            assert "conversation_id:91001" in query
            return {
                "data": [
                    {
                        "id": "91002",
                        "text": "Reply de outro usuario",
                        "created_at": "2024-04-01T11:00:00Z",
                        "conversation_id": "91001",
                        "author_id": "u-b",
                        "referenced_tweets": [{"type": "replied_to", "id": "91001"}],
                        "in_reply_to_user_id": "u-a",
                    }
                ],
                "includes": {
                    "users": [
                        {"id": "u-a", "username": "owner", "name": "Owner User"},
                        {"id": "u-b", "username": "alice", "name": "Alice"},
                    ]
                },
                "meta": {},
            }

    monkeypatch.setattr(x_api_sync, "XApiClient", FakeXApiClient)

    with app.app_context():
        archive = db.session.get(Archive, 1)
        result = x_api_sync.sync_archive_from_x_api(
            archive=archive,
            username="owner",
            include_replies=True,
            include_conversation_replies=True,
            incremental_sync=False,
        )
        assert result["fetched_conversation_replies"] == 1
        posts = Post.query.filter_by(archive_id=archive.id).all()
        assert len(posts) == 2
        reply = Post.query.filter_by(external_post_id="91002").first()
        assert reply is not None
        assert reply.author_handle == "alice"
        assert reply.reply_to_user_handle == "owner"


def test_api_sync_route_posts_and_flashes_success(client, app, monkeypatch):
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo API Route")
    assert create_response.status_code == 200

    import app.archives.routes as archive_routes

    called = {"count": 0}

    def fake_sync(**kwargs):
        called["count"] += 1
        assert kwargs["username"] == "demo"
        return {
            "source_username": "demo",
            "fetched_raw_tweets": 10,
            "imported_count": 8,
            "duplicate_count": 2,
            "pages_loaded": 1,
        }

    monkeypatch.setattr(archive_routes, "sync_archive_from_x_api", fake_sync)

    response = client.post(
        "/archives/1/api-sync",
        data={
            "username": "demo",
            "user_id": "",
            "import_mode": "merge",
            "include_replies": "y",
            "max_pages": "1",
            "max_results": "50",
            "conversation_max_pages": "2",
            "conversation_reply_window_days": "30",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Sincronização da API concluída".encode("utf-8") in response.data
    assert called["count"] == 1


def test_ops_metrics_endpoint_returns_summary(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo Ops")
    assert create_response.status_code == 200

    payload = [
        {
            "tweet": {
                "id_str": "99001",
                "full_text": "Ops metric test",
                "created_at": "Tue Apr 02 10:00:00 +0000 2024",
                "lang": "en",
                "entities": {"hashtags": [], "user_mentions": [], "urls": []},
            }
        }
    ]
    _import_json(client, archive_id=1, payload=payload, mode="merge")

    response = client.get("/ops/metrics?hours=24&limit=5", follow_redirects=True)
    assert response.status_code == 200
    data = response.get_json()
    assert data["window_hours"] == 24
    assert any(item["operation_type"] == "import" for item in data["summary"])
    assert len(data["recent"]) >= 1


def test_x_api_sync_conversation_replies_multi_page(client, app, monkeypatch):
    login_response = _login(client)
    assert login_response.status_code == 200
    create_response = _create_archive(client, name="Arquivo API Replies Multi")
    assert create_response.status_code == 200

    from app.services import x_api_sync

    tokens = []
    start_times = []

    class FakeXApiClient:
        def __init__(self, bearer_token, base_url, timeout_seconds, **kwargs):
            pass

        def resolve_user(self, username=None, user_id=None):
            return {"id": "u-m", "username": "owner", "name": "Owner User"}

        def get_user_tweets(self, user_id, max_results=100, pagination_token=None, since_id=None):
            return {
                "data": [
                    {
                        "id": "92001",
                        "text": "Post base paginado",
                        "created_at": "2024-04-01T10:00:00Z",
                        "conversation_id": "92001",
                        "author_id": "u-m",
                    }
                ],
                "includes": {"users": [{"id": "u-m", "username": "owner", "name": "Owner User"}]},
                "meta": {},
            }

        def search_recent_tweets(self, query, max_results=100, pagination_token=None, start_time=None):
            tokens.append(pagination_token)
            start_times.append(start_time)
            assert "conversation_id:92001" in query
            if pagination_token is None:
                return {
                    "data": [
                        {
                            "id": "92002",
                            "text": "Reply pagina 1",
                            "created_at": "2024-04-01T11:00:00Z",
                            "conversation_id": "92001",
                            "author_id": "u-b1",
                            "referenced_tweets": [{"type": "replied_to", "id": "92001"}],
                            "in_reply_to_user_id": "u-m",
                        }
                    ],
                    "includes": {
                        "users": [
                            {"id": "u-m", "username": "owner", "name": "Owner User"},
                            {"id": "u-b1", "username": "alice", "name": "Alice"},
                        ]
                    },
                    "meta": {"next_token": "next-1"},
                }
            return {
                "data": [
                    {
                        "id": "92003",
                        "text": "Reply pagina 2",
                        "created_at": "2024-04-01T11:10:00Z",
                        "conversation_id": "92001",
                        "author_id": "u-b2",
                        "referenced_tweets": [{"type": "replied_to", "id": "92001"}],
                        "in_reply_to_user_id": "u-m",
                    }
                ],
                "includes": {
                    "users": [
                        {"id": "u-m", "username": "owner", "name": "Owner User"},
                        {"id": "u-b2", "username": "bob", "name": "Bob"},
                    ]
                },
                "meta": {},
            }

    monkeypatch.setattr(x_api_sync, "XApiClient", FakeXApiClient)

    with app.app_context():
        archive = db.session.get(Archive, 1)
        result = x_api_sync.sync_archive_from_x_api(
            archive=archive,
            username="owner",
            include_replies=True,
            include_conversation_replies=True,
            incremental_sync=False,
            max_pages=1,
            max_results=50,
            conversation_max_pages=3,
            conversation_reply_window_days=7,
        )
        assert result["fetched_conversation_replies"] == 2
        assert result["conversation_pages_loaded"] == 2
        assert result["timeline_pages_loaded"] == 1
        assert result["conversation_reply_window_days"] == 7
        assert tokens == [None, "next-1"]
        assert all(value for value in start_times)
        posts = Post.query.filter_by(archive_id=archive.id).all()
        assert len(posts) == 3


def test_favicon_is_served(client):
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"


def test_security_headers_present(client):
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    csp = response.headers.get("Content-Security-Policy") or ""
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert response.headers.get("Strict-Transport-Security") is None
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("X-Request-ID")


def test_request_id_echoes_valid_incoming_header(client, app):
    app.config["REQUEST_ID_ACCEPT_INCOMING"] = True
    request_id = "req-1234abcd-xyz"
    response = client.get("/healthz", headers={"X-Request-ID": request_id}, follow_redirects=False)
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == request_id


def test_request_id_replaces_invalid_incoming_header(client, app):
    app.config["REQUEST_ID_ACCEPT_INCOMING"] = True
    response = client.get("/healthz", headers={"X-Request-ID": "??"}, follow_redirects=False)
    assert response.status_code == 200
    generated = response.headers.get("X-Request-ID") or ""
    assert generated != "??"
    assert len(generated) >= 8


def test_hsts_header_for_https_requests(client, app):
    app.config["ENABLE_HSTS"] = True
    app.config["HSTS_INCLUDE_SUBDOMAINS"] = True
    app.config["HSTS_PRELOAD"] = False
    app.config["HSTS_MAX_AGE_SECONDS"] = 31536000

    response = client.get("/auth/login", base_url="https://example.com", follow_redirects=False)
    assert response.status_code == 200
    hsts = response.headers.get("Strict-Transport-Security")
    assert hsts is not None
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


def test_hsts_respects_trusted_proxy_flag(client, app):
    app.config["ENABLE_HSTS"] = True
    app.config["TRUST_PROXY_HEADERS"] = False

    insecure = client.get(
        "/auth/login",
        base_url="http://example.com",
        headers={"X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert insecure.status_code == 200
    assert insecure.headers.get("Strict-Transport-Security") is None

    app.config["TRUST_PROXY_HEADERS"] = True
    secure = client.get(
        "/auth/login",
        base_url="http://example.com",
        headers={"X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert secure.status_code == 200
    assert secure.headers.get("Strict-Transport-Security") is not None


def test_force_https_rejects_non_localhost_insecure_requests(client, app):
    app.config["FORCE_HTTPS"] = True
    response = client.get("/auth/login", base_url="http://example.com", follow_redirects=False)
    assert response.status_code == 400
    assert b"HTTPS required" in response.data


def test_user_management_protects_default_user(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200

    users_page = client.get("/auth/users", follow_redirects=True)
    assert users_page.status_code == 200
    assert b"admin@example.com" in users_page.data

    create_user = client.post(
        "/auth/users/new",
        data={
            "username": "operator1",
            "email": "operator1@example.com",
            "password": "StrongPass123!",
            "role": "viewer",
        },
        follow_redirects=True,
    )
    assert create_user.status_code == 200
    assert "Usuário criado".encode("utf-8") in create_user.data

    with app.app_context():
        operator = User.query.filter_by(email="operator1@example.com").first()
        assert operator is not None
        operator_id = operator.id

    edit_operator = client.post(
        f"/auth/users/{operator_id}/edit",
        data={"username": "operator2", "email": "operator2@example.com", "role": "admin"},
        follow_redirects=True,
    )
    assert edit_operator.status_code == 200
    assert "Usuário atualizado".encode("utf-8") in edit_operator.data

    change_default_password = client.post(
        "/auth/users/1/password",
        data={"password": "NewAdminPass123!", "password_confirm": "NewAdminPass123!"},
        follow_redirects=True,
    )
    assert change_default_password.status_code == 200
    assert b"Senha atualizada" in change_default_password.data

    blocked_default_edit = client.post(
        "/auth/users/1/edit",
        data={"username": "admin2", "email": "admin2@example.com"},
        follow_redirects=True,
    )
    assert blocked_default_edit.status_code == 200
    assert "Usuário padrão não pode ter nome ou email alterado".encode("utf-8") in blocked_default_edit.data

    blocked_default_delete = client.post("/auth/users/1/delete", follow_redirects=True)
    assert blocked_default_delete.status_code == 200
    assert "Usuário padrão não pode ser removido".encode("utf-8") in blocked_default_delete.data

    delete_operator = client.post(f"/auth/users/{operator_id}/delete", follow_redirects=True)
    assert delete_operator.status_code == 200
    assert "Usuário removido".encode("utf-8") in delete_operator.data

    logout_response = client.post("/auth/logout", follow_redirects=True)
    assert logout_response.status_code == 200

    relogin = client.post(
        "/auth/login",
        data={"email": "admin@example.com", "password": "NewAdminPass123!"},
        follow_redirects=True,
    )
    assert relogin.status_code == 200
    assert b"Dashboard" in relogin.data


def test_rbac_viewer_restrictions_and_self_password_change(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200

    create_archive = _create_archive(client, name="Arquivo RBAC")
    assert create_archive.status_code == 200

    create_viewer = client.post(
        "/auth/users/new",
        data={
            "username": "viewer1",
            "email": "viewer1@example.com",
            "password": "ViewerPass123!",
            "role": "viewer",
        },
        follow_redirects=True,
    )
    assert create_viewer.status_code == 200
    assert "Usuário criado".encode("utf-8") in create_viewer.data

    with app.app_context():
        viewer = User.query.filter_by(email="viewer1@example.com").first()
        assert viewer is not None
        assert viewer.role == "viewer"
        viewer_id = viewer.id

    logout_response = client.post("/auth/logout", follow_redirects=True)
    assert logout_response.status_code == 200

    login_viewer = client.post(
        "/auth/login",
        data={"email": "viewer1@example.com", "password": "ViewerPass123!"},
        follow_redirects=True,
    )
    assert login_viewer.status_code == 200

    dashboard = client.get("/dashboard", follow_redirects=True)
    assert dashboard.status_code == 200
    assert "Usuários".encode("utf-8") not in dashboard.data

    blocked_users = client.get("/auth/users", follow_redirects=True)
    assert blocked_users.status_code == 200
    assert "Ação permitida apenas para administradores".encode("utf-8") in blocked_users.data

    blocked_create_archive = client.get("/archives/create", follow_redirects=True)
    assert blocked_create_archive.status_code == 200
    assert "Ação permitida apenas para administradores".encode("utf-8") in blocked_create_archive.data

    allowed_archive_detail = client.get("/archives/1", follow_redirects=True)
    assert allowed_archive_detail.status_code == 200
    assert b"Arquivo RBAC" in allowed_archive_detail.data

    blocked_admin_password = client.get("/auth/users/1/password", follow_redirects=True)
    assert blocked_admin_password.status_code == 200
    assert "Ação permitida apenas para administradores".encode("utf-8") in blocked_admin_password.data

    change_own_password = client.post(
        f"/auth/users/{viewer_id}/password",
        data={"password": "ViewerPass456!", "password_confirm": "ViewerPass456!"},
        follow_redirects=True,
    )
    assert change_own_password.status_code == 200
    assert "Senha atualizada para viewer1".encode("utf-8") in change_own_password.data


def test_users_list_pagination(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200
    app.config["USER_ITEMS_PER_PAGE"] = 5

    with app.app_context():
        for idx in range(1, 9):
            user = User(name=f"user{idx}", email=f"user{idx}@example.com", role=User.ROLE_VIEWER)
            user.set_password("StrongPass123!")
            db.session.add(user)
        db.session.commit()

    page_two = client.get("/auth/users?page=2", follow_redirects=True)
    assert page_two.status_code == 200
    assert b"user5@example.com" in page_two.data
    assert b"user1@example.com" not in page_two.data


def test_conversation_pagination_in_post_detail(client, app):
    login_response = _login(client)
    assert login_response.status_code == 200
    app.config["CONVERSATION_ITEMS_PER_PAGE"] = 3

    with app.app_context():
        archive = Archive(name="Conv Archive", status="ready")
        db.session.add(archive)
        db.session.flush()

        posts = []
        for idx in range(1, 8):
            post = Post(
                archive_id=archive.id,
                external_post_id=f"conv-{idx}",
                text_raw=f"Conversation post {idx}",
                text_normalized=f"conversation post {idx}",
                conversation_id="conv-thread-1",
                created_at=datetime(2024, 4, idx, 10, 0, 0),
                is_reply=idx > 1,
                has_media=False,
                has_links=False,
            )
            db.session.add(post)
            posts.append(post)
        db.session.commit()
        target_post_id = posts[0].id

    page_one = client.get(f"/posts/{target_post_id}?conversation_page=1", follow_redirects=True)
    assert page_one.status_code == 200
    assert b"conv-1" in page_one.data
    assert b"conv-4" not in page_one.data

    page_two = client.get(f"/posts/{target_post_id}?conversation_page=2", follow_redirects=True)
    assert page_two.status_code == 200
    assert b"conv-4" in page_two.data
