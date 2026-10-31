"""Shadow DOM serializado pelo Chrome no MHTML (`<template shadowmode="open">`): a tela precisa
anexá-lo de novo e os extratores precisam ler o texto de dentro."""
from apps.artifacts.extractors import extract_full_text
from apps.artifacts.extractors.shadow_dom import achatar_shadow_dom, declarar_shadow_dom

PAGINA = (
    '<html><body><h1>Extratos</h1>'
    '<app-extrato><template shadowmode="open"><bb-label> Saldo Anterior </bb-label>'
    '<bb-row><template shadowmode="open"><span>-R$ 2.825,71</span><slot></slot></template>linha</bb-row>'
    '</template></app-extrato>'
    '<template id="modelo"><p>não é shadow root</p></template>'
    '</body></html>'
)


def test_sem_achatar_os_extratores_nao_veem_o_shadow_root():
    assert "Saldo Anterior" not in extract_full_text(PAGINA)


def test_achatar_tira_so_os_templates_de_shadow_root():
    plano = achatar_shadow_dom(PAGINA)
    texto = extract_full_text(plano)
    assert "Saldo Anterior" in texto and "-R$ 2.825,71" in texto and "linha" in texto
    assert '<template id="modelo"><p>não é shadow root</p></template>' in plano
    assert "shadowmode" not in plano


def test_achatar_sem_shadow_root_devolve_o_mesmo_html():
    html = '<template><b>x</b></template><p>y</p>'
    assert achatar_shadow_dom(html) is html


def test_declarar_troca_o_atributo_antigo_pelo_atual():
    html = declarar_shadow_dom(PAGINA)
    assert html.count('<template shadowrootmode="open">') == 2
    assert '<template id="modelo">' in html
    assert declarar_shadow_dom('<template shadowrootmode="open">') == '<template shadowrootmode="open">'
