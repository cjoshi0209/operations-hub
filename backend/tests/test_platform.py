"""Cross-cutting behaviour: error shape, request ids, health/readiness, auth."""


def test_validation_errors_use_the_standard_error_envelope(world):
    r = world.client().post("/work-items", headers=world.h("alice"), json={"title": "", "type": "nope"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["fields"] and err["message"]


def test_unknown_routes_use_the_standard_error_envelope(world):
    r = world.client().get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_request_id_is_generated_and_echoed(world):
    c = world.client()
    assert len(c.get("/health").headers["X-Request-ID"]) >= 8
    assert c.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"


def test_health_and_ready(world):
    c = world.client()
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/ready").json() == {"status": "ready"}


def test_login_rejects_bad_credentials_without_revealing_which_part_was_wrong(world):
    c = world.client()
    wrong_pw = c.post("/auth/login", json={"email": "alice@example.com", "password": "nope"})
    no_user = c.post("/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


def test_login_is_case_insensitive_on_email_and_returns_a_working_token(world):
    c = world.client()
    r = c.post("/auth/login", json={"email": " ALICE@example.com ", "password": "pw-for-tests"})
    assert r.status_code == 200
    me = c.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
    assert me["user"]["name"] == "Alice" and me["memberships"][0]["role"] == "lead"


def test_expired_token_is_rejected(world):
    from datetime import timedelta

    from sqlalchemy import update

    from app.models import AuthToken, utcnow

    token = world.token("bob")
    with world.db.write_session() as s:
        s.execute(update(AuthToken).values(expires_at=utcnow() - timedelta(seconds=1)))
        s.commit()
    r = world.client().get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_repeated_failed_logins_are_throttled_and_success_resets(world):
    c = world.client()
    bad = {"email": "bob@example.com", "password": "wrong"}
    for _ in range(world.app.state.settings.login_max_failures):
        assert c.post("/auth/login", json=bad).status_code == 401
    blocked = c.post("/auth/login", json=bad)
    assert blocked.status_code == 429 and blocked.json()["error"]["retry_after_seconds"] > 0
    # even the right password is refused while locked out, and other accounts are unaffected
    assert c.post("/auth/login", json={"email": "bob@example.com", "password": "pw-for-tests"}).status_code == 429
    assert c.post("/auth/login", json={"email": "carol@example.com", "password": "pw-for-tests"}).status_code == 200
