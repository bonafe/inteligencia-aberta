from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.text import slugify

from .forms import RegistrationForm
from .models import Membership, Organization


def registro(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.email = form.cleaned_data["email"]
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
