# Copyright (c) Microsoft. All rights reserved.
"""Ingesta del reporte de créditos adjuntado por el analista.

Foundry monta los archivos subidos a la sesión en un directorio del contenedor,
pero la ruta exacta no está documentada de forma fiable (variaba entre el cwd
del proceso y el home directory en pruebas reales). Por eso, en lugar de asumir
una sola ruta, se buscan las raíces candidatas más probables -- igual que hace
el sample oficial `06-files`, que le da al modelo un `get_cwd()` para descubrir
la ruta real en vez de asumirla-- y se exponen herramientas de exploración cruda
como respaldo si la búsqueda automática no encuentra el archivo.

El contenido se extrae con Azure Document Intelligence (`prebuilt-layout`) en
formato markdown, que preserva las tablas con su estructura fila/columna.
"""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from pathlib import Path

import httpx
from agent_framework import tool
from azure.identity.aio import DefaultAzureCredential
from pydantic import Field
from typing_extensions import Annotated

_DIRS_EXCLUIDOS = {".git", "__pycache__", "site-packages", "node_modules", ".venv"}

_API_VERSION = "2024-11-30"
_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 240.0
_MAX_CHARS = 400_000

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".heif": "image/heif",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".html": "text/html",
}


@lru_cache(maxsize=1)
def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential()


def _raices_candidatas() -> list[Path]:
    """Raíces donde Foundry podría haber montado los archivos de la sesión."""
    candidatas = [Path.cwd()]
    try:
        candidatas.append(Path.home())
    except Exception:
        pass
    candidatas.append(Path("/tmp"))
    if os.getenv("AGENT_FILES_DIR"):
        candidatas.append(Path(os.environ["AGENT_FILES_DIR"]))

    vistas: list[Path] = []
    for c in candidatas:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r.is_dir() and r not in vistas:
            vistas.append(r)
    return vistas


def _buscar_por_nombre(nombre_archivo: str) -> Path | None:
    for raiz in _raices_candidatas():
        candidata = raiz / nombre_archivo
        if candidata.is_file():
            return candidata
        for ruta in raiz.rglob(nombre_archivo):
            if ruta.is_file() and not _DIRS_EXCLUIDOS.intersection(ruta.parts):
                return ruta
    return None


def _endpoint() -> str:
    endpoint = os.getenv("DOCUMENT_INTELLIGENCE_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "DOCUMENT_INTELLIGENCE_ENDPOINT no está configurado en el entorno del agente."
        )
    return endpoint.rstrip("/")


def _resolver_ruta(nombre_archivo: str) -> Path:
    # El nombre lo elige el modelo a partir de la entrada del usuario: impedir path
    # traversal fuera de las raíces candidatas resolviendo primero y comparando después.
    if any(parte == ".." for parte in Path(nombre_archivo).parts):
        raise ValueError(f"Ruta inválida: {nombre_archivo}")
    ruta = _buscar_por_nombre(nombre_archivo)
    if ruta is None:
        raise FileNotFoundError(
            f"No se encontró '{nombre_archivo}' en ninguna de las rutas candidatas "
            f"({', '.join(str(r) for r in _raices_candidatas())}). Usa listar_documentos_adjuntos "
            "o explorar_directorio para localizarlo."
        )
    return ruta


async def _analizar_documento(contenido: bytes, content_type: str) -> str:
    endpoint = _endpoint()
    token = await _credential().get_token(_TOKEN_SCOPE)
    headers = {"Authorization": f"Bearer {token.token}", "Content-Type": content_type}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        inicio = await client.post(
            f"{endpoint}/documentintelligence/documentModels/prebuilt-layout:analyze",
            params={"api-version": _API_VERSION, "outputContentFormat": "markdown"},
            headers=headers,
            content=contenido,
        )
        inicio.raise_for_status()

        operacion = inicio.headers.get("operation-location")
        if not operacion:
            raise RuntimeError("Document Intelligence no devolvió 'operation-location'.")

        limite = asyncio.get_running_loop().time() + _POLL_TIMEOUT_S
        while True:
            await asyncio.sleep(_POLL_INTERVAL_S)
            respuesta = await client.get(
                operacion, headers={"Authorization": f"Bearer {token.token}"}
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            estado = cuerpo.get("status")

            if estado == "succeeded":
                return cuerpo["analyzeResult"]["content"]
            if estado == "failed":
                detalle = cuerpo.get("error", {}).get("message", "sin detalle")
                raise RuntimeError(f"Document Intelligence falló al analizar: {detalle}")
            if asyncio.get_running_loop().time() > limite:
                raise TimeoutError(
                    f"El análisis superó {_POLL_TIMEOUT_S:.0f}s sin completarse."
                )


@tool(approval_mode="never_require")
def listar_documentos_adjuntos() -> str:
    """Lista los documentos que el analista adjuntó a la sesión (PDF, imágenes, Office).

    Busca en las rutas más probables donde Foundry monta los archivos de la sesión.
    Úsala para descubrir el nombre exacto del reporte de créditos antes de leerlo. Si no
    encuentra nada, usa directorio_actual() y explorar_directorio() para buscar a mano.
    """
    encontrados: list[Path] = []
    for raiz in _raices_candidatas():
        for ruta in raiz.rglob("*"):
            if (
                ruta.is_file()
                and ruta.suffix.lower() in _CONTENT_TYPES
                and not _DIRS_EXCLUIDOS.intersection(ruta.parts)
                and ruta not in encontrados
            ):
                encontrados.append(ruta)

    if not encontrados:
        raices = ", ".join(str(r) for r in _raices_candidatas())
        return (
            "No hay documentos adjuntos en las rutas candidatas revisadas "
            f"({raices}). Prueba directorio_actual() y explorar_directorio() para buscar "
            "manualmente, o pide al analista que confirme que subió el archivo a esta sesión."
        )

    lineas = [f"- {ruta} ({ruta.stat().st_size / 1024:.0f} KB)" for ruta in sorted(encontrados)]
    return "Documentos disponibles en la sesión:\n" + "\n".join(lineas)


@tool(approval_mode="never_require")
def directorio_actual() -> str:
    """Devuelve el directorio de trabajo actual del proceso del agente.

    Úsala junto con explorar_directorio() si listar_documentos_adjuntos no encuentra
    el archivo, para inspeccionar manualmente dónde quedó montado.
    """
    return os.getcwd()


@tool(approval_mode="never_require")
def explorar_directorio(
    ruta: Annotated[
        str,
        Field(description="Ruta absoluta del directorio a explorar (ej. la devuelta por directorio_actual())."),
    ],
) -> str:
    """Lista el contenido (archivos y subcarpetas) de una ruta absoluta del contenedor.

    Herramienta de respaldo para localizar manualmente el reporte adjunto cuando
    listar_documentos_adjuntos() no lo encuentra en las rutas candidatas habituales.
    """
    try:
        entradas = sorted(os.listdir(ruta))
    except OSError as exc:
        return f"No se pudo listar '{ruta}': {exc}"
    if not entradas:
        return f"'{ruta}' está vacío."
    return f"Contenido de '{ruta}':\n" + "\n".join(entradas)


@tool(approval_mode="never_require")
async def leer_reporte_credito(
    nombre_archivo: Annotated[
        str,
        Field(description="Nombre del archivo adjunto, tal como aparece en listar_documentos_adjuntos."),
    ],
) -> str:
    """Extrae el texto y las tablas de un reporte de créditos adjunto.

    Devuelve el contenido en markdown; las tablas se conservan con su estructura
    original de filas y columnas, por lo que las cifras pueden leerse sin ambigüedad.
    """
    ruta = _resolver_ruta(nombre_archivo)
    content_type = _CONTENT_TYPES.get(ruta.suffix.lower())
    if content_type is None:
        return f"Formato no soportado: '{ruta.suffix}'. Formatos válidos: {', '.join(sorted(_CONTENT_TYPES))}."

    try:
        contenido = await _analizar_documento(ruta.read_bytes(), content_type)
    except Exception as exc:  # se devuelve al modelo para que informe al analista
        return f"No se pudo extraer el contenido de '{nombre_archivo}': {exc}"

    if len(contenido) > _MAX_CHARS:
        contenido = (
            contenido[:_MAX_CHARS]
            + f"\n\n[CONTENIDO TRUNCADO: el documento supera {_MAX_CHARS} caracteres. "
            "Informa al analista que el análisis cubre solo la primera parte del reporte.]"
        )
    return contenido
