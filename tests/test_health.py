from fastapi.testclient import TestClient

from awning.context import AppContext


def test_healthz_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_anthropic_client_has_explicit_timeout(ctx: AppContext) -> None:
    assert ctx.extractor.client.timeout == 90
    assert ctx.agent.client.timeout == 90
