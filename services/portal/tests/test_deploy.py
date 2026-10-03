"""Garantias de implantação: /health responde sem login."""

import pytest

pytestmark = pytest.mark.django_db


def test_health_sem_login(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
