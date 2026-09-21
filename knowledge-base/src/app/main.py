"""API de indexación SharePoint -> Document Intelligence -> embeddings -> Azure AI Search."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException

from app.config import config
from app.services import document_intelligence, embeddings, search
from app.services.sharepoint import Archivo, ClienteSharePoint
from app.utils.chunking import dividir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("indexacion")

app = FastAPI(title="Indexación SharePoint -> Azure AI Search")

_candado = asyncio.Lock()
_estado: dict = {"estado": "inactivo"}


def _cargar_procesados() -> dict[str, str]:
    if config.ruta_estado.exists():
        return json.loads(config.ruta_estado.read_text(encoding="utf-8"))
    return {}


def _guardar_procesados(procesados: dict[str, str]) -> None:
    config.ruta_estado.write_text(json.dumps(procesados, indent=2), encoding="utf-8")


def _id_chunk(archivo_id: str, posicion: int) -> str:
    return f"{hashlib.sha1(archivo_id.encode()).hexdigest()}-{posicion}"


async def _indexar_archivo(cliente: ClienteSharePoint, archivo: Archivo) -> int:
    tipo = document_intelligence.content_type(archivo.nombre)
    if tipo is None:
        log.warning("Formato no soportado, se omite: %s", archivo.nombre)
        return 0

    contenido = await cliente.descargar(archivo.id)
    markdown = await document_intelligence.extraer_markdown(config, contenido, tipo)
    chunks = dividir(markdown, config.chunk_max_tokens, config.chunk_overlap_tokens)
    if not chunks:
        return 0

    vectores = await embeddings.generar(config, chunks)
    await search.eliminar_por_url(config, archivo.url)
    await search.subir(
        config,
        [
            {
                "id": _id_chunk(archivo.id, posicion),
                "title": archivo.nombre,
                "url": archivo.url,
                "content": texto,
                "embedding": vector,
            }
            for posicion, (texto, vector) in enumerate(zip(chunks, vectores))
        ],
    )
    log.info("%s -> %d chunks", archivo.nombre, len(chunks))
    return len(chunks)


async def _ejecutar(completo: bool) -> None:
    async with _candado:
        _estado.update(
            estado="ejecutando",
            modo="completo" if completo else "incremental",
            inicio=datetime.now(timezone.utc).isoformat(),
            documentos=0,
            omitidos=0,
            chunks=0,
            errores=[],
            fin=None,
        )
        try:
            procesados = _cargar_procesados()
            async with ClienteSharePoint(config) as cliente:
                archivos = await cliente.listar_archivos()
                log.info("Archivos encontrados en SharePoint: %d", len(archivos))

                for archivo in archivos:
                    if not completo and procesados.get(archivo.id) == archivo.modificado:
                        _estado["omitidos"] += 1
                        continue
                    try:
                        _estado["chunks"] += await _indexar_archivo(cliente, archivo)
                        _estado["documentos"] += 1
                        procesados[archivo.id] = archivo.modificado
                    except Exception as exc:
                        log.exception("Error indexando %s", archivo.nombre)
                        _estado["errores"].append(
                            f"{archivo.nombre}: {type(exc).__name__}: {exc}"
                        )

            _guardar_procesados(procesados)
            _estado["estado"] = "completado"
        except Exception as exc:
            log.exception("La indexación falló")
            _estado.update(estado="fallido", error=str(exc))
        finally:
            _estado["fin"] = datetime.now(timezone.utc).isoformat()


def _lanzar(tareas: BackgroundTasks, completo: bool) -> dict:
    if _candado.locked():
        raise HTTPException(status_code=409, detail="Ya hay una indexación en curso.")
    tareas.add_task(_ejecutar, completo)
    return {"mensaje": "Indexación iniciada.", "modo": "completo" if completo else "incremental"}


@app.post("/index/run")
async def indexar_todo(tareas: BackgroundTasks) -> dict:
    """Reprocesa todos los documentos de la carpeta de SharePoint."""
    return _lanzar(tareas, completo=True)


@app.post("/index/incremental")
async def indexar_incremental(tareas: BackgroundTasks) -> dict:
    """Reprocesa solo los documentos nuevos o modificados desde la última corrida."""
    return _lanzar(tareas, completo=False)


@app.get("/index/status")
async def estado() -> dict:
    try:
        total = await search.contar(config)
    except Exception as exc:
        total = f"no disponible ({exc})"
    return {**_estado, "indice": config.search_index_name, "chunks_en_indice": total}
