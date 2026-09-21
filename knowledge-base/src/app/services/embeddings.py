"""Generación de embeddings con text-embedding-3-large."""

from __future__ import annotations

import httpx

from app.config import Config

API_VERSION = "2024-10-21"
TAMANO_LOTE = 16


async def generar(config: Config, textos: list[str]) -> list[list[float]]:
    endpoint = config.azure_openai_endpoint.rstrip("/")
    url = f"{endpoint}/openai/deployments/{config.embedding_deployment}/embeddings"
    headers = {"api-key": config.azure_ai_key, "Content-Type": "application/json"}

    vectores: list[list[float]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as http:
        for inicio in range(0, len(textos), TAMANO_LOTE):
            lote = textos[inicio : inicio + TAMANO_LOTE]
            respuesta = await http.post(
                url,
                params={"api-version": API_VERSION},
                headers=headers,
                json={"input": lote, "dimensions": config.embedding_dimensions},
            )
            respuesta.raise_for_status()
            datos = sorted(respuesta.json()["data"], key=lambda d: d["index"])
            vectores.extend(d["embedding"] for d in datos)
    return vectores
