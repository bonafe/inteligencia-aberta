"""Primeiro boot idempotente de uma instância — para a automação de deploy.

    manage.py bootstrap_instancia

Faz, nesta ordem, e pode rodar quantas vezes for preciso (a segunda execução não
altera nada):

1. aplica as migrations;
2. cria os buckets do Garage (S3) que faltarem;
3. **opcionalmente** cria o superusuário a partir de DJANGO_SUPERUSER_USERNAME / _EMAIL /
   _PASSWORD (com sua organização pessoal), SOMENTE se ainda não existir nenhum usuário. Sem
   as três variáveis nada é criado e o primeiro cadastro em /registro/ vira o administrador;
4. gera a chave Ed25519 da instância (ADR 010), se ainda não existir.

O passo 3 só olha para "existe algum usuário": uma vez que a instância tem dono,
mudar as variáveis não cria nem altera ninguém. O cadastro público é **aberto**
(`REGISTRO_ABERTO`): com as variáveis o dono nasce aqui; sem elas, quem se cadastrar
primeiro vira o administrador — então faça esse primeiro cadastro antes de expor a porta.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from minio import Minio

from apps.accounts.services import criar_organizacao_individual

# Mesmo nome que o orchestrator cria ao subir e que o portal usa como padrão.
BUCKETS = ["inteligencia-aberta-mhtml"]


class Command(BaseCommand):
    help = "Migrations, buckets do Garage (S3) e superusuário inicial (idempotente)."

    def handle(self, *args, **options):
        self.stdout.write("1/4 migrations")
        call_command("migrate", interactive=False, verbosity=0)

        self.stdout.write("2/4 buckets do Garage (S3)")
        self._buckets()

        self.stdout.write("3/4 superusuário")
        self._superusuario()

        self.stdout.write("4/4 chave da instância")
        self._chave_da_instancia()
        self.stdout.write(self.style.SUCCESS("Instância pronta."))

    def _buckets(self):
        client = Minio(
            os.getenv("S3_ENDPOINT", "garage:3900"),
            access_key=os.getenv("S3_ACCESS_KEY", ""),
            secret_key=os.getenv("S3_SECRET_KEY", ""),
            secure=False,
        )
        for nome in BUCKETS:
            if client.bucket_exists(nome):
                self.stdout.write(f"  {nome}: já existe")
            else:
                client.make_bucket(nome)
                self.stdout.write(f"  {nome}: criado")

    def _chave_da_instancia(self):
        from apps.federacao.chaves import garantir_chave_ativa

        chave, criada = garantir_chave_ativa()
        self.stdout.write(f"  {chave.did}{'  (criada agora)' if criada else ''}")

    def _superusuario(self):
        User = get_user_model()
        if User.objects.exists():
            self.stdout.write("  já há usuários — nada a fazer")
            return

        username = os.getenv("DJANGO_SUPERUSER_USERNAME", "").strip()
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "")
        if not (username and email and password):
            # Sem as variáveis o administrador nasce pelo primeiro cadastro em /registro/
            # (que é aberto): quem abrir a instância primeiro vira o dono. É o comportamento
            # esperado; só vale ficar atento a não expor a porta antes de fazer o primeiro cadastro.
            self.stdout.write(
                "  DJANGO_SUPERUSER_* ausentes: nenhum superusuário criado — o primeiro cadastro "
                "em /registro/ será o administrador da instância."
            )
            return
        if password.lower().startswith(("change_me", "substitua-por")):
            raise CommandError("DJANGO_SUPERUSER_PASSWORD está com o valor de exemplo.")

        with transaction.atomic():
            user = User.objects.create_superuser(username=username, email=email, password=password)
            criar_organizacao_individual(user)
        self.stdout.write(f"  superusuário '{username}' criado")
