from django.http import JsonResponse

from apps.extensoes.dominio import artefatos_visiveis

LIMITE = 200


def pontos(request):
    """Artefatos da organização do usuário que têm `latitude` e `longitude` no conteúdo."""
    resultado = []
    for artefato in artefatos_visiveis(request).order_by("-classified_at")[:2000]:
        conteudo = artefato.content if isinstance(artefato.content, dict) else {}
        try:
            lat, lon = float(conteudo["latitude"]), float(conteudo["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        resultado.append({"id": str(artefato.pk), "label": str(conteudo.get("nome") or conteudo.get("logradouro") or artefato.artifact_type),
                          "lat": lat, "lon": lon, "classification": artefato.classification_level})
        if len(resultado) >= LIMITE:
            break
    return JsonResponse({"results": resultado})
