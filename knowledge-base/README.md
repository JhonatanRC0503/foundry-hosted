# knowledge-base — indexación SharePoint → Azure AI Search

API propia (FastAPI) que extrae documentos de una carpeta de SharePoint, los procesa con
Document Intelligence, genera embeddings y los indexa en Azure AI Search para que el agente
`sectorial` los consuma como tool (`search_conocimiento`).

Vive **fuera** de los agentes: es infraestructura de indexación reutilizable, no parte del
runtime del agente.

## Por qué esta solución y no el indexer nativo de Azure AI Search

El indexer nativo "SharePoint in Microsoft 365" no es viable en este proyecto: la app de
SharePoint vive en el tenant de Microsoft 365 (`c9661c28...`) y el servicio de Search está en
otro tenant de Azure (`932d69f5...`). Ese indexer exige que ambos estén en el mismo tenant
(usa una federated credential de la identidad administrada del Search). Por eso se construyó
esta API propia con Microsoft Graph.

## Pipeline

```
SharePoint (Graph API)
  → descarga PDF/DOCX/XLSX
  → Document Intelligence (prebuilt-layout, outputContentFormat=markdown; tablas ya vienen
    estructuradas como HTML <table>, no hace falta convertirlas a mano)
  → chunking con overlap (src/app/utils/chunking.py)
  → embeddings (text-embedding-3-large, cuenta hub-foundry-hosted)
  → Azure AI Search (mergeOrUpload por chunk; se borran los chunks previos del mismo
    documento antes de subir los nuevos, para no dejar residuos al reindexar)
```

## Estructura

```
knowledge-base/
├── .env                      # config real (nunca se commitea)
├── .env.example               # plantilla sin secretos
├── estado.json                 # (se genera solo) registro de lastModifiedDateTime por archivo, usado por /index/incremental
├── indice/
│   └── agente-sharepoint-index.json   # esquema del índice (5 campos: id, title, url, content, embedding)
├── scripts/
│   ├── configurar_llaves.sh    # rellena AZURE_AI_KEY y SEARCH_API_KEY en .env desde Azure CLI
│   └── crear_indice.py         # crea/actualiza el índice en Azure AI Search a partir del JSON de indice/
└── src/
    ├── requirements.txt
    └── app/
        ├── main.py             # FastAPI: /index/run, /index/incremental, /index/status
        ├── config.py           # carga .env (pydantic-settings)
        ├── services/
        │   ├── sharepoint.py            # auth Graph (client credentials) + listar/descargar archivos
        │   ├── document_intelligence.py # extracción a markdown
        │   ├── embeddings.py            # llamadas a text-embedding-3-large
        │   └── search.py                # subir / borrar por url / contar documentos
        └── utils/
            └── chunking.py       # división en chunks con overlap, sin cortar tablas
```

## Índice en Azure AI Search

`agente-sharepoint-index`, 5 campos: `id`, `title`, `url`, `content`, `embedding`
(`Collection(Edm.Single)`, 3072 dimensiones, HNSW + coseno, vectorizer de Azure OpenAI, config
semántica). El agente lo consulta con búsqueda híbrida (`query_type: vector_simple_hybrid`,
`top_k: 5`) sin tener que calcular embeddings del lado del agente — el vectorizer del índice lo
hace por él.

## Configuración inicial (una sola vez)

```bash
cd knowledge-base
cp .env.example .env          # completar SHAREPOINT_CLIENT_SECRET a mano (no se automatiza)
python3 -m venv .venv
.venv/bin/pip install -r src/requirements.txt

# Rellena AZURE_AI_KEY y SEARCH_API_KEY leyéndolas de Azure (requiere az login)
bash scripts/configurar_llaves.sh

# Crea el índice en Azure AI Search (idempotente: también sirve para actualizar el esquema)
.venv/bin/python scripts/crear_indice.py
```

## Levantar la API

```bash
cd knowledge-base
PYTHONPATH=src .venv/bin/python -m uvicorn app.main:app --port 8080
```

## Indexar cuando subes un documento nuevo a SharePoint

Con la API corriendo:

```bash
# Solo procesa archivos nuevos o modificados desde la última corrida (recomendado)
curl -X POST http://127.0.0.1:8080/index/incremental

# Reprocesa TODOS los documentos de la carpeta, aunque no hayan cambiado
curl -X POST http://127.0.0.1:8080/index/run

# Ver progreso / resultado (documentos procesados, chunks, errores)
curl http://127.0.0.1:8080/index/status
```

`/index/incremental` es el flujo normal: compara `lastModifiedDateTime` de cada archivo contra
lo guardado en `estado.json` y solo reprocesa lo nuevo o cambiado. `/index/run` es para forzar
una reindexación completa (p. ej. si cambiaste el chunking o el esquema del índice).

Ambos endpoints corren en background (`BackgroundTasks`): la llamada responde de inmediato con
`{"mensaje": "Indexación iniciada."}` y hay que consultar `/index/status` para ver cuándo
termina. Solo puede haber una indexación en curso a la vez (si ya hay una corriendo, el segundo
POST responde 409).

## Notas

- Formatos soportados: `.pdf`, `.docx`, `.xlsx` (configurable con `SHAREPOINT_EXTENSIONES` en `.env`).
- Si falla la descarga de un archivo puntual (error transitorio de red con SharePoint), queda
  registrado en `errores` dentro de `/index/status` pero no detiene el resto del lote; basta con
  volver a correr `/index/incremental` para que reintente solo los que fallaron o cambiaron.
- Es una API interna/POC: sin capa de auth propia, reintentos automáticos ni contenedor —
  se ejecuta manualmente cuando hace falta reindexar.
