#!/usr/bin/env python3
"""Crea o actualiza el índice de Azure AI Search a partir de indice/*.json."""

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ / ".env")

esquema = json.loads((RAIZ / "indice" / "agente-sharepoint-index.json").read_text("utf-8"))
esquema["name"] = os.environ["SEARCH_INDEX_NAME"]

# El vectorizador permite que el agente consulte el índice enviando texto, sin calcular embeddings.
for vectorizador in esquema["vectorSearch"]["vectorizers"]:
    vectorizador["azureOpenAIParameters"].update(
        resourceUri=os.environ["AZURE_OPENAI_ENDPOINT"],
        deploymentId=os.environ["EMBEDDING_DEPLOYMENT"],
        apiKey=os.environ["AZURE_AI_KEY"],
    )

respuesta = httpx.put(
    f"{os.environ['SEARCH_ENDPOINT'].rstrip('/')}/indexes/{esquema['name']}",
    params={"api-version": "2024-07-01"},
    headers={"api-key": os.environ["SEARCH_API_KEY"], "Content-Type": "application/json"},
    json=esquema,
    timeout=60.0,
)

if respuesta.is_error:
    print(respuesta.text, file=sys.stderr)
    sys.exit(1)
print(f"Índice '{esquema['name']}' listo.")
