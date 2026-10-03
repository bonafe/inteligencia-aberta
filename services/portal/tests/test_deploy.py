"""Garantias de implantação: /health responde sem login e o registro fecha
depois do primeiro usuário (instância exposta não aceita cadastro de estranhos)."""

import pytest
from django.test import override_settings

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

DADOS = {
    "username": "novo",
    "email": "novo@exemplo.com",
    "password1": "Senha-forte-123",
    "password2": "Senha-forte-123",
}


def test_health_sem_login(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@override_settings(REGISTRO_ABERTO=False)
def test_registro_primeiro_usuario_vira_superusuario(client):
    resp = client.post("/registro/", DADOS)
    assert resp.status_code == 302
    assert User.objects.get(username="novo").is_superuser


@override_settings(REGISTRO_ABERTO=False)
def test_registro_fechado_depois_do_primeiro(client):
    User.objects.create_user(username="dono", password="x")
    assert client.get("/registro/").status_code == 403
    assert client.post("/registro/", DADOS).status_code == 403
    assert not User.objects.filter(username="novo").exists()


@override_settings(REGISTRO_ABERTO=True)
def test_registro_aberto_por_flag(client):
    User.objects.create_user(username="dono", password="x")
    assert client.post("/registro/", DADOS).status_code == 302
    assert User.objects.filter(username="novo", is_superuser=False).exists()


from config.segredos import segredo_invalido, validar_segredos

# ── segredos ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("valor", [None, "", "  ", "CHANGE_ME", "change_me_agora", "substitua-por-senha-segura"])
def test_segredo_invalido(valor):
    assert segredo_invalido(valor)


def test_segredo_valido():
    assert not segredo_invalido("k3J8-aleatorio")


def test_validar_segredos_lista_todos_os_problemas(monkeypatch):
    monkeypatch.setenv("A", "CHANGE_ME")
    monkeypatch.setenv("B", "ok-de-verdade")
    monkeypatch.delenv("C", raising=False)
    with pytest.raises(RuntimeError) as exc:
        validar_segredos(["A", "B", "C"])
    assert "A, C" in str(exc.value) and "B" not in str(exc.value).split(":")[1].split(".")[0]
