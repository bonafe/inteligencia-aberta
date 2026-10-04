"""Validação do endereço de um par (`endpoint_controle`) — defesa contra SSRF.

O portal faz requisições para o endereço que o **par informa** (no convite). Sem
validar, um par malicioso apontaria o portal para serviços internos ou para o endpoint
de metadados de uma nuvem. Regras:

- só `http`/`https`, com host, **sem** usuário/senha, query ou fragmento;
- endereços **link-local** (`169.254.0.0/16`, `fe80::/10`, onde ficam os metadados de
  nuvem), multicast, reservados e `0.0.0.0` são **sempre** recusados;
- **HTTP** só para rede privada/VPN (RFC 1918, loopback, CGNAT `100.64.0.0/10` do
  Tailscale, ULA) ou nomes de rede interna (`localhost`, rótulo único, `.local`,
  `.internal`, `.lan`, `.home.arpa`, `.ts.net`); fora disso exige HTTPS, a menos que
  `FEDERACAO_PERMITE_HTTP_PUBLICO` esteja ligado.

Limite conhecido: a checagem de **nome** acontece aqui, não na hora de conectar —
um nome que depois resolva para outro endereço (DNS rebinding) não é pego.
"""

import ipaddress
from urllib.parse import urlsplit

from django.conf import settings

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_SUFIXOS_INTERNOS = (".local", ".internal", ".lan", ".home.arpa", ".ts.net")


def _ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _rede_privada(ip) -> bool:
    return ip.is_private or ip.is_loopback or (ip.version == 4 and ip in _CGNAT)


def _nome_interno(host: str) -> bool:
    return host == "localhost" or "." not in host or host.endswith(_SUFIXOS_INTERNOS)


def validar_endpoint(endpoint: str) -> str:
    """Devolve o endpoint normalizado (sem `/` final) ou levanta `ValueError`."""
    try:
        partes = urlsplit((endpoint or "").strip())
        host = (partes.hostname or "").lower()
        partes.port  # noqa: B018 — levanta ValueError se a porta for inválida
    except ValueError:
        raise ValueError("endereço do par inválido") from None
    if partes.scheme not in ("http", "https") or not host:
        raise ValueError("o endereço do par deve ser uma URL http(s)")
    if partes.username is not None or partes.password is not None:
        raise ValueError("o endereço do par não pode ter usuário ou senha")
    if partes.path not in ("", "/") or partes.query or partes.fragment:
        raise ValueError("informe só a base do endereço do par (sem caminho, query ou fragmento)")

    ip = _ip(host)
    if ip is not None:
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise ValueError("endereço do par recusado (link-local, multicast ou reservado)")
        privado = _rede_privada(ip)
    else:
        privado = _nome_interno(host)

    if partes.scheme == "http" and not privado and not getattr(settings, "FEDERACAO_PERMITE_HTTP_PUBLICO", False):
        raise ValueError("fora de rede privada/VPN o par precisa usar https")
    return f"{partes.scheme}://{partes.netloc}"
