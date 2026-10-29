"""HTML enxuto: a entrada da estruturação por LLM (extractors/html_enxuto.py)."""
from apps.artifacts.extractors.html_enxuto import enxugar


def test_remove_o_que_nao_e_dado():
    html = """<html><head><title>t</title><style>td{}</style></head><body>
      <script>rastreio()</script><!-- comentário -->
      <nav><a href="/menu">Menu</a></nav>
      <div style="display:none">oculto</div>
      <form><input type="hidden" name="token" value="segredo"></form>
      <p onclick="x()" style="color:red" data-track="1">Conteúdo</p>
    </body></html>"""
    saida = enxugar(html)
    assert "Conteúdo" in saida
    for lixo in ("rastreio", "comentário", "Menu", "oculto", "segredo", "onclick", "style", "data-track"):
        assert lixo not in saida


def test_mantem_estrutura_e_classes_da_tabela():
    html = """<table class="lancamentos"><tr><th>Data</th><th>Valor</th></tr>
      <tr><td class="tituloLancamento" colspan="2"><span><b>Saúde</b></span></td></tr>
      <tr><td>12/10</td><td>10,00</td></tr></table>"""
    saida = enxugar(html)
    assert '<table class="lancamentos">' in saida
    assert '<td class="tituloLancamento" colspan="2">Saúde</td>' in saida
    assert "<span>" not in saida and "<b>" not in saida
    assert "<tr><td>12/10</td><td>10,00</td></tr>" in saida


def test_nao_junta_linhas_identicas():
    """Duas compras iguais no mesmo dia são dois lançamentos (por isso sem o dedup do dom2parser)."""
    linha = "<tr><td>12/10</td><td>CAFÉ</td><td>5,00</td></tr>"
    saida = enxugar(f"<table>{linha}{linha}</table>")
    assert saida.count("CAFÉ") == 2


def test_tira_linhas_e_elementos_vazios_mas_mantem_celula_vazia():
    html = """<table><tr><td></td><td class=""></td></tr>
      <tr><td></td><td>SALDO</td></tr></table><div><p> </p></div>"""
    saida = enxugar(html)
    assert saida.count("<tr>") == 1
    assert "<tr><td></td><td>SALDO</td></tr>" in saida
    assert "<p>" not in saida and 'class=""' not in saida


def test_texto_depois_de_elemento_removido_nao_se_perde():
    saida = enxugar("<div>antes<img src='x.png'>depois<p></p>fim</div>")
    assert "antes" in saida and "depois" in saida and "fim" in saida


def test_html_vazio():
    assert enxugar("") == ""
    assert enxugar("   ") == ""
