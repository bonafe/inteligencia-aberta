import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_embedding_model = None
_qdrant_client = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from fastembed import TextEmbedding
        model_name = getattr(settings, "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        logger.info("Carregando modelo de embedding '%s' (primeira carga faz download ~120MB)...", model_name)
        _embedding_model = TextEmbedding(model_name=model_name)
        logger.info("Modelo de embedding pronto.")
    return _embedding_model


def get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        host = getattr(settings, "QDRANT_HOST", "qdrant")
        port = getattr(settings, "QDRANT_PORT", 6333)
        _qdrant_client = QdrantClient(host=host, port=port)
    return _qdrant_client


def ensure_collection(client, tenant_id: str, dim: int) -> str:
    """Garante que a collection do tenant existe. Retorna o nome."""
    from qdrant_client.models import Distance, VectorParams

    name = f"ia_{tenant_id.replace('-', '')}"
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        logger.info("Criando collection Qdrant '%s' (dim=%d)", name, dim)
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
    return name
