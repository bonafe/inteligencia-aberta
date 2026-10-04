from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from .forms import RegistrationForm
from .models import Organization
from .services import criar_organizacao_individual


def orgs_do_usuario(user):
    """Organizações às quais o usuário pertence (via Membership).

    Fonte única de verdade para isolamento de tenant nas views — usado para
    filtrar querysets de Artifact e afins, garantindo que um usuário nunca
    acesse dado de organização que não é dele.
    """
    return Organization.objects.filter(memberships__user=user)


def registro(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    User = get_user_model()
    # Fechado depois do primeiro usuário: numa instância exposta, estranhos não
    # podem criar conta (e organização) por conta própria. Vale para GET e POST.
    primeiro_usuario = not User.objects.exists()
    if not settings.REGISTRO_ABERTO and not primeiro_usuario:
        return render(request, "accounts/registro_fechado.html", status=403)
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.email = form.cleaned_data["email"]

            # Regra de especificação (Fase 0): o primeiro usuário a se cadastrar
            # no sistema vira superusuário. Isso faz o próprio registro bootstrapar
            # o admin — quem instala o sistema não precisa rodar
            # `manage.py createsuperuser` à parte. O risco aceito é operacional,
            # não de código: quem sobe o ambiente deve criar sua conta antes de
            # expor a porta do portal publicamente (mesma janela de setup que
            # qualquer app self-hosted com bootstrap por primeiro-usuário).
            if primeiro_usuario or not User.objects.exists():
                user.is_staff = True
                user.is_superuser = True

            user.save()
            criar_organizacao_individual(user)
            login(request, user)
            return redirect("dashboard")
    else:
        form = RegistrationForm()
    return render(request, "accounts/registro.html", {"form": form, "primeiro_usuario": primeiro_usuario})


@login_required
def dashboard(request):
    return render(request, "accounts/dashboard.html")


class EntrarView(LoginView):
    """Tela de login. Numa instância **sem nenhum usuário** não há com o que entrar: vai direto
    para o cadastro, onde o primeiro usuário vira o administrador."""

    template_name = "accounts/login.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated and not get_user_model().objects.exists():
            return redirect("registro")
        return super().dispatch(request, *args, **kwargs)
