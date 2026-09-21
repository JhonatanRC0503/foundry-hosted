"""Chunking del markdown de Document Intelligence.

Las tablas que devuelve prebuilt-layout se tratan como bloques atómicos: nunca se
parten a la mitad, salvo que por sí solas excedan el límite del modelo de embeddings.
"""

from __future__ import annotations

import re
from typing import Iterator

import tiktoken

_CODIFICADOR = tiktoken.get_encoding("cl100k_base")
_TABLA = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)
_LIMITE_EMBEDDING = 8000


def contar_tokens(texto: str) -> int:
    return len(_CODIFICADOR.encode(texto))


def _partir_duro(texto: str, max_tokens: int) -> list[str]:
    tokens = _CODIFICADOR.encode(texto)
    return [
        _CODIFICADOR.decode(tokens[i : i + max_tokens]) for i in range(0, len(tokens), max_tokens)
    ]


def _parrafos(texto: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]


def _bloques(contenido: str) -> Iterator[tuple[str, bool]]:
    """Devuelve (texto, es_tabla) recorriendo el markdown en orden."""
    posicion = 0
    for coincidencia in _TABLA.finditer(contenido):
        for parrafo in _parrafos(contenido[posicion : coincidencia.start()]):
            yield parrafo, False
        yield coincidencia.group(0), True
        posicion = coincidencia.end()
    for parrafo in _parrafos(contenido[posicion:]):
        yield parrafo, False


def _cola(bloques: list[str], overlap_tokens: int) -> tuple[list[str], int]:
    seleccion: list[str] = []
    total = 0
    for bloque in reversed(bloques):
        tokens = contar_tokens(bloque)
        if total + tokens > overlap_tokens:
            break
        seleccion.insert(0, bloque)
        total += tokens
    return seleccion, total


def dividir(contenido: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    chunks: list[str] = []
    actual: list[str] = []
    tokens_actual = 0

    def cerrar() -> None:
        nonlocal actual, tokens_actual
        if actual:
            chunks.append("\n\n".join(actual))
            actual, tokens_actual = [], 0

    for bloque, es_tabla in _bloques(contenido):
        tokens = contar_tokens(bloque)

        if tokens > max_tokens:
            cerrar()
            if es_tabla and tokens <= _LIMITE_EMBEDDING:
                chunks.append(bloque)
            else:
                chunks.extend(_partir_duro(bloque, max_tokens))
            continue

        if tokens_actual + tokens > max_tokens and actual:
            chunks.append("\n\n".join(actual))
            actual, tokens_actual = _cola(actual, overlap_tokens)

        actual.append(bloque)
        tokens_actual += tokens

    cerrar()
    return chunks
