"""Escritura y conteo de documentos en Azure AI Search."""

from __future__ import annotations

import httpx

from app.config import Config

API_VERSION = "2024-07-01"
TAMANO_LOTE = 50


def _base(config: Config) -> tuple[str, dict[str, str]]:
    url = f"{config.search_endpoint.rstrip('/')}/indexes/{config.search_index_name}/docs"
    return url, {"api-key": config.search_api_key, "Content-Type": "application/json"}


async def _enviar(config: Config, acciones: list[dict]) -> None:
    url, headers = _base(config)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as http:
        for inicio in range(0, len(acciones), TAMANO_LOTE):
            respuesta = await http.post(
                f"{url}/index",
                params={"api-version": API_VERSION},
                headers=headers,
                json={"value": acciones[inicio : inicio + TAMANO_LOTE]},
            )
            respuesta.raise_for_status()


async def subir(config: Config, documentos: list[dict]) -> None:
    await _enviar(config, [{"@search.action": "mergeOrUpload", **doc} for doc in documentos])


async def eliminar_por_url(config: Config, url_documento: str) -> int:
    """Borra los chunks previos de un documento para que la reindexación no deje residuos."""
    url, headers = _base(config)
    filtro = "url eq '{}'".format(url_documento.replace("'", "''"))

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http:
        respuesta = await http.post(
            f"{url}/search",
            params={"api-version": API_VERSION},
            headers=headers,
            json={"search": "*", "filter": filtro, "select": "id", "top": 1000},
        )
        respuesta.raise_for_status()
        ids = [item["id"] for item in respuesta.json()["value"]]

    if ids:
        await _enviar(config, [{"@search.action": "delete", "id": i} for i in ids])
    return len(ids)


async def contar(config: Config) -> int:
    url, headers = _base(config)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http:
        respuesta = await http.get(
            f"{url}/$count", params={"api-version": API_VERSION}, headers=headers
        )
        respuesta.raise_for_status()
        return int(respuesta.text.strip().lstrip("\ufeff"))
