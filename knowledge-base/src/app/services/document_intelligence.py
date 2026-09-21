"""Extracción de contenido con Document Intelligence (prebuilt-layout, salida markdown).

El modelo ya devuelve las tablas estructuradas dentro del markdown, así que no se
hace ningún postprocesamiento sobre ellas.
"""

from __future__ import annotations

import asyncio

import httpx

from app.config import Config

API_VERSION = "2024-11-30"
INTERVALO_SONDEO_S = 2.0
TIMEOUT_SONDEO_S = 300.0

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".html": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def content_type(nombre: str) -> str | None:
    for extension, tipo in CONTENT_TYPES.items():
        if nombre.lower().endswith(extension):
            return tipo
    return None


async def extraer_markdown(config: Config, contenido: bytes, tipo: str) -> str:
    endpoint = config.document_intelligence_endpoint.rstrip("/")
    headers = {"Ocp-Apim-Subscription-Key": config.azure_ai_key, "Content-Type": tipo}

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as http:
        inicio = await http.post(
            f"{endpoint}/documentintelligence/documentModels/prebuilt-layout:analyze",
            params={"api-version": API_VERSION, "outputContentFormat": "markdown"},
            headers=headers,
            content=contenido,
        )
        inicio.raise_for_status()

        operacion = inicio.headers.get("operation-location")
        if not operacion:
            raise RuntimeError("Document Intelligence no devolvió 'operation-location'.")

        limite = asyncio.get_running_loop().time() + TIMEOUT_SONDEO_S
        while True:
            await asyncio.sleep(INTERVALO_SONDEO_S)
            respuesta = await http.get(
                operacion, headers={"Ocp-Apim-Subscription-Key": config.azure_ai_key}
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()

            if cuerpo.get("status") == "succeeded":
                return cuerpo["analyzeResult"]["content"]
            if cuerpo.get("status") == "failed":
                detalle = cuerpo.get("error", {}).get("message", "sin detalle")
                raise RuntimeError(f"Document Intelligence falló: {detalle}")
            if asyncio.get_running_loop().time() > limite:
                raise TimeoutError(f"El análisis superó {TIMEOUT_SONDEO_S:.0f}s.")
