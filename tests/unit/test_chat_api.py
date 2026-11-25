"""Unit tests for the LLM chat and usage API."""

import pytest

from licenselens.api.deps import get_session
from licenselens.llm.gateway import LLMResponse


class FakeSession:
    async def commit(self):
        pass

    async def rollback(self):
        pass


class FakeGateway:
    async def complete(self, messages, temperature=0.2):
        assert messages[0]["role"] == "system"
        assert "LicenseLens" in messages[0]["content"]

        return LLMResponse(
            content="The MIT licence is permissive.",
            provider="gemini",
            model="test-model",
            tokens_in=20,
            tokens_out=12,
            latency_ms=100,
        )


class FakeLlmCallRepository:
    records = []

    def __init__(self, session):
        self.session = session

    async def record(self, **kwargs):
        self.records.append(kwargs)

    async def get_usage(self):
        return {
            "total_calls": 1,
            "total_tokens_in": 20,
            "total_tokens_out": 12,
            "total_cost_inr": 0.001,
        }


@pytest.fixture
def chat_client(client, monkeypatch):
    from licenselens.api.routes import chat as chat_routes

    async def override_session():
        yield FakeSession()

    client.app.dependency_overrides[get_session] = override_session

    client.app.state.llm_gateway = FakeGateway()

    FakeLlmCallRepository.records = []

    monkeypatch.setattr(
        chat_routes,
        "LlmCallRepository",
        FakeLlmCallRepository,
    )

    yield client

    client.app.dependency_overrides.pop(get_session, None)


def test_chat_endpoint(chat_client):
    response = chat_client.post(
        "/v1/chat",
        json={
            "message": "Explain the MIT licence.",
            "temperature": 0.2,
            "purpose": "licence_explanation",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["answer"] == "The MIT licence is permissive."
    assert data["provider"] == "gemini"
    assert data["model"] == "test-model"
    assert data["tokens_in"] == 20
    assert data["tokens_out"] == 12
    assert data["fallback_used"] is False
    assert data["request_id"]

    assert len(FakeLlmCallRepository.records) == 1
    assert FakeLlmCallRepository.records[0]["status"] == "success"


def test_chat_rejects_empty_message(chat_client):
    response = chat_client.post(
        "/v1/chat",
        json={"message": ""},
    )

    assert response.status_code == 422


def test_chat_rejects_invalid_temperature(chat_client):
    response = chat_client.post(
        "/v1/chat",
        json={
            "message": "Explain MIT.",
            "temperature": 3.0,
        },
    )

    assert response.status_code == 422


def test_usage_endpoint(chat_client):
    response = chat_client.get("/v1/usage")

    assert response.status_code == 200

    data = response.json()

    assert data["total_calls"] == 1
    assert data["total_tokens_in"] == 20
    assert data["total_tokens_out"] == 12
    assert data["total_cost_inr"] == 0.001
