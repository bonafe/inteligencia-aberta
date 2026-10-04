"""Catálogo **curado** de modelos para a tela de instalação — só sugestões.

Não há uma API oficial e estável de listagem do registry do Ollama, e uma instância pode estar numa rede sem
saída para a internet; por isso o catálogo é uma lista versionada aqui, editável por quem mantém o projeto. Ela
**pode ficar desatualizada** (um modelo pode mudar de nome ou sair do registry): a tela sempre aceita qualquer
`nome:tag` digitado, e o Ollama responde com erro claro se o modelo não existir. Os tamanhos são **aproximados**.
"""

CATALOGO = (
    {"nome": "qwen3.5:9b", "uso": "chat", "gb": 6.0, "nota": "o modelo usado nos exemplos e na estruturação deste projeto"},
    {"nome": "llama3.2:3b", "uso": "chat", "gb": 2.0, "nota": "leve; roda em máquina modesta"},
    {"nome": "llama3.1:8b", "uso": "chat", "gb": 4.9, "nota": "uso geral"},
    {"nome": "qwen2.5:7b", "uso": "chat", "gb": 4.7, "nota": "bom em português e em dados estruturados"},
    {"nome": "mistral:7b", "uso": "chat", "gb": 4.1, "nota": "uso geral"},
    {"nome": "gemma2:9b", "uso": "chat", "gb": 5.4, "nota": "uso geral"},
    {"nome": "phi3:mini", "uso": "chat", "gb": 2.2, "nota": "pequeno"},
    {"nome": "nomic-embed-text", "uso": "embeddings", "gb": 0.3, "nota": "vetores para busca semântica"},
    {"nome": "bge-m3", "uso": "embeddings", "gb": 1.2, "nota": "vetores multilíngues"},
)
