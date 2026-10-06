from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import get_object_or_404, redirect, render

from .forms import RegistrationForm
from .membros import PAPEIS_CONCEDIVEIS, ErroMembro, adicionar_membro
from .models import Membership, Organization
from .permissoes import exige_admin, orgs_onde_e_admin
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


@login_required
def membros(request, org_id=None):
    """Quem administra a organização lista os membros e adiciona gente (por nome de usuário), com workspace opcional."""
    from apps.agora.models import Workspace

    if org_id is None:
        primeira = orgs_onde_e_admin(request.user).order_by("name").first()
        if primeira is None:
            return render(request, "accounts/membros.html", {"sem_org": True}, status=403)
        return redirect("membros", org_id=primeira.pk)
    org = get_object_or_404(Organization, pk=org_id)
    exige_admin(request.user, org)

    if request.method == "POST":
        workspace = None
        if request.POST.get("workspace"):
            workspace = Workspace.objects.filter(pk=request.POST["workspace"], organization=org).first()
        try:
            if request.POST.get("workspace") and workspace is None:
                raise ErroMembro("Workspace não encontrado nesta organização.")
            r = adicionar_membro(
                request.user, org, request.POST.get("username", ""), request.POST.get("papel", ""),
                workspace=workspace, papel_workspace=request.POST.get("papel_workspace"),
            )
        except ErroMembro as erro:
            messages.error(request, str(erro))
        else:
            nome = r.usuario.get_username()
            if r.ja_era_membro:
                messages.info(request, f"{nome} já era membro desta organização (papel mantido: {r.membership.get_role_display()}).")
            else:
                messages.success(request, f"{nome} foi adicionado(a) como {r.membership.get_role_display()}.")
            if workspace is not None:
                messages.success(request, f"{nome} tem acesso a “{workspace.title}” como {Workspace.Role(r.workspace_papel).label}.")
        return redirect("membros", org_id=org.pk)

    return render(request, "accounts/membros.html", {
        "org": org,
        "orgs": orgs_onde_e_admin(request.user).order_by("name"),
        "membros": Membership.objects.filter(organization=org).select_related("user").order_by("user__username"),
        "workspaces": Workspace.objects.filter(organization=org, archived_at__isnull=True).order_by("title"),
        "papeis": [(p.value, p.label) for p in PAPEIS_CONCEDIVEIS],
        "papeis_workspace": [(v, l) for v, l in Workspace.Role.choices if v != "owner"],
    })
