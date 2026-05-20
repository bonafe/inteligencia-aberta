from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User

_INPUT_ATTRS = {"style": "width:100%;padding:.6rem .75rem;border:1px solid #d1d5db;border-radius:6px;font-size:.95rem;"}


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True, label="E-mail")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update(_INPUT_ATTRS)
