from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class LoginRequiredMiddleware:
    """Exige sessão autenticada em toda URL fora de uma allowlist explícita.

    Secure-by-default: em vez de lembrar de decorar cada view com @login_required
    (o esquecimento disso foi o que abriu o IDOR nas views de artifacts), o padrão
    passa a ser "protegido". Abrir uma rota ao público é uma decisão explícita —
    adicionar o prefixo a EXEMPT_PREFIXES abaixo.

    O Django só ganhou um LoginRequiredMiddleware nativo no 5.1; o projeto está no
    5.0.6, por isso este é escrito à mão.

    Nota: rotas autenticadas por outros mecanismos (JWT nas rotas de token, token
    de serviço na API interna, sessão de staff no admin) entram na allowlist —
    cada uma faz sua própria verificação de credencial.
    """

    # Prefixos de path liberados da exigência de sessão.
    EXEMPT_PREFIXES = (
        "/entrar/",
        "/sair/",
        "/registro/",
        "/admin/",                       # autenticação própria do admin (is_staff)
        "/static/",
        "/api/v1/token/",                # emissão/refresh de JWT (credencial no corpo)
        "/artifacts/api/v1/artefatos/",  # canal serviço-a-serviço (X-Internal-Token)
        "/api/schema/",                  # documentação da API (drf-spectacular) —
        "/api/docs/",                    # pública por decisão: só descreve os
        "/api/redoc/",                   # endpoints, não expõe dado nenhum.
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not request.user.is_authenticated and not self._is_exempt(path):
            login_url = getattr(settings, "LOGIN_URL", None) or reverse("login")
            return redirect(f"{login_url}?next={path}")
        return self.get_response(request)

    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)
