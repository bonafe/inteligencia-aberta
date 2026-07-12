from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.text import slugify

from .forms import RegistrationForm
from .models import Membership, Organization


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
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.email = form.cleaned_data["email"]
            # Registro é aberto e cria um usuário comum. Superusuário/staff é
            # provisionado apenas via `manage.py createsuperuser` — auto-promover
            # o primeiro cadastro seria escalada de privilégio por corrida.
            user.save()
            org = Organization.objects.create(
                name=user.username,
                slug=slugify(user.username),
                org_type=Organization.Type.INDIVIDUAL,
                owner=user,
            )
            Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
            login(request, user)
            return redirect("dashboard")
    else:
        form = RegistrationForm()
    return render(request, "accounts/registro.html", {"form": form})


@login_required
def dashboard(request):
    return render(request, "accounts/dashboard.html")
