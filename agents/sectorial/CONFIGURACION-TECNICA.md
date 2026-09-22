# Configuración técnica del agente `sectorial`

Este documento describe, con detalle técnico, todo lo que se construyó sobre el agente base
(`azure.yaml` + `main.py`) para darle acceso a búsqueda web, base de conocimiento (SharePoint vía
Azure AI Search) e ingesta del reporte de créditos adjunto (Azure Document Intelligence). Incluye
los recursos creados, las conexiones, y **exactamente qué permisos se otorgaron y a quién**.

## 1. Recursos de Azure involucrados

| Recurso | Nombre | Resource Group | Región | Notas |
|---|---|---|---|---|
| Foundry account (`AIServices`) | `hub-foundry-hosted` | `rg-foundry-hosted` | Central US | Ya existía. Expone también Document Intelligence (`FormRecognizer`) como parte del mismo recurso multi-servicio. |
| Foundry project | `proj-foundry-hosted` | — | — | Endpoint: `https://hub-foundry-hosted.services.ai.azure.com/api/projects/proj-foundry-hosted` |
| Azure AI Search | `srch-sectorial-22l6c` | `rg-foundry-hosted` | Central US | **Creado para este proyecto.** SKU `Basic`. |
| Modelo desplegado | `gpt-5-mini` (2025-08-07) | — | — | Nota: `azure.yaml` declara `gpt-5.4-mini`/2026-03-17, pero el modelo realmente aprovisionado y en uso es `gpt-5-mini` (drift entre IaC y estado real). |

Subscripción: `655c9df4-c812-488e-b6ae-84cafe9d5b3f` ("Azure G&S (Patrocinio 2026) - Interno
Analítica & AI II"). Tenant: `932d69f5-a800-4527-9dcf-eabfe5c25af4` ("G&S Gestión y Sistemas").

## 2. Web search (Grounding con Bing)

- **Tipo:** `web_search` — herramienta *connectionless*, no requiere conexión ni credenciales propias.
- **Configuración:** declarada directamente en el toolbox (ver sección 4). Foundry gestiona el
  acceso a Bing internamente.
- **Permisos otorgados:** ninguno adicional — viene incluido en la plataforma.
- **Advertencia de cumplimiento (mostrada por el propio portal de Foundry):** las consultas de
  búsqueda salen del límite de cumplimiento de Azure hacia la infraestructura de Bing, sujeta a sus
  propios términos de uso y política de privacidad. Ver conversación previa sobre el riesgo para
  datos de clientes (nombre de empresa, cifras del reporte) si se filtran en el texto de búsqueda.

## 3. Azure AI Search (base de conocimiento / SharePoint)

### 3.1 Servicio y conexión

```bash
# Servicio creado
az search service create --name srch-sectorial-22l6c \
  --resource-group rg-foundry-hosted --sku Basic --location centralus \
  --identity-type SystemAssigned
```

Se creó también el índice `sectorial-sharepoint-index` (campos `id`, `metadata_spo_item_*`,
`content`) que recibirá los documentos de SharePoint una vez configurado el indexer (ver sección 6).

La conexión hacia el proyecto Foundry (tipo `CognitiveSearch`) se registró vía ARM REST directo
(fue la primera conexión del proyecto, por lo que `azd ai connection create` no podía autodescubrir
el contexto):

```
PUT /subscriptions/{sub}/resourceGroups/rg-foundry-hosted/providers/Microsoft.CognitiveServices
    /accounts/hub-foundry-hosted/projects/proj-foundry-hosted/connections/sectorial-search-conn
    ?api-version=2025-04-01-preview

{
  "properties": {
    "category": "CognitiveSearch",
    "authType": "ApiKey",
    "target": "https://srch-sectorial-22l6c.search.windows.net/",
    "credentials": { "key": "<admin-key-del-servicio>" }
  }
}
```

- **Nombre de la conexión:** `sectorial-search-conn`
- **Permisos otorgados:** ninguno vía RBAC — la autenticación es por **API key** (admin key del
  servicio de Search), almacenada como credencial de la conexión en Foundry (no en el código del
  agente).

### 3.2 Pendiente: indexado automático desde SharePoint

El índice existe pero **está vacío**. Está bloqueado en espera de un app registration en Entra ID
(`sectorial-sharepoint-indexer`) con permisos de Microsoft Graph `Files.Read.All` + `Sites.Read.All`
y consentimiento de administrador — ver hilo de correo con el admin del tenant. Una vez se reciba el
`client_id`/`client_secret`, se completa con: data source tipo `sharepoint`, indexer con programación
automática, y mapeo de campos hacia `sectorial-sharepoint-index`.

## 4. El toolbox (`agent-tools`)

Declarado en [`azure.yaml`](azure.yaml) como servicio `host: azure.ai.toolbox`:

```yaml
agent-tools:
  host: azure.ai.toolbox
  tools:
    - type: web_search
      name: web_search
    - type: azure_ai_search
      name: search_conocimiento
      azure_ai_search:
        indexes:
          - project_connection_id: sectorial-search-conn
            index_name: sectorial-sharepoint-index
            query_type: simple
            top_k: 5
```

Endpoint MCP (versión por defecto, siempre la última publicada):

```
https://hub-foundry-hosted.services.ai.azure.com/api/projects/proj-foundry-hosted/toolboxes/agent-tools/mcp?api-version=v1
```

El agente lo consume vía `FoundryToolbox` (paquete `agent-framework-foundry-hosting`), resolviendo el
endpoint en runtime a partir de `FOUNDRY_PROJECT_ENDPOINT` + `TOOLBOX_NAME=agent-tools` — no hay URLs
hardcodeadas en el código.

**Permisos otorgados:** ninguno adicional al agente para llamar al toolbox — el mismo token de la
identidad administrada del agente (scope `https://ai.azure.com/.default`) autentica la llamada MCP;
Foundry internamente usa las credenciales de cada conexión (la API key de Search en este caso) para
llamar al servicio real, sin exponerlas al agente.

## 5. Document Intelligence (ingesta del reporte de créditos)

### 5.1 Por qué no hay un recurso nuevo

`hub-foundry-hosted` es una cuenta `AIServices` (multi-servicio) y ya expone Document Intelligence
bajo el nombre histórico `FormRecognizer`:

```
https://hub-foundry-hosted.cognitiveservices.azure.com/
```

No se creó ningún recurso — solo se usó el endpoint que ya existía.

### 5.2 Cómo llega el archivo al agente

Cuando el analista sube el reporte (PDF/imagen/Office) a la sesión del agente hosted (por el
Playground o `azd ai agent files upload`), **Foundry monta ese archivo en el directorio de trabajo
del contenedor**. El agente lo lee con operaciones normales de filesystem — no hace falta ninguna
API de subida de archivos en el código.

### 5.3 Herramientas locales agregadas

Implementadas en [`reporte_credito.py`](src/agent-framework-agent-basic-responses/reporte_credito.py)
como *local tools* del Agent Framework (`@tool`), no como parte del toolbox MCP:

| Función | Qué hace |
|---|---|
| `listar_documentos_adjuntos()` | Lista los archivos disponibles en la sesión (para que el modelo descubra el nombre exacto del reporte). |
| `leer_reporte_credito(nombre_archivo)` | Envía el archivo a Document Intelligence (`prebuilt-layout`, `outputContentFormat=markdown`) y devuelve el texto con las tablas preservadas en HTML (`<table><tr><td>`), con estructura fila/columna intacta. |

**Por qué Document Intelligence y no `file_search`:** `file_search` hace retrieval vectorial por
fragmentos (chunks) del documento — apto para texto narrativo, pero rompe la relación fila/columna de
una tabla financiera (una celda de "1,234" pierde el contexto de a qué cuenta/año pertenece). Document
Intelligence extrae la tabla completa con su estructura, necesaria para cálculos financieros
confiables.

**Límites verificados (no son un riesgo con reportes de 5-8 páginas):**
- Tier `S0` (Standard) del recurso: hasta 2,000 páginas y 500 MB por análisis (confirmado con
  `az cognitiveservices account show` → `sku.name=S0`). El tier `F0` (gratuito) corta silenciosamente
  a las 2 primeras páginas — **no** es el caso aquí.
- `_MAX_CHARS = 400_000` en el código: un reporte de 5 páginas produce ~13,500 caracteres (~3.4% del
  límite); es una válvula de seguridad para documentos anormalmente grandes, no un límite operativo.

### 5.4 Permisos otorgados — el punto crítico

```bash
az role assignment create \
  --role "Cognitive Services User" \
  --assignee "<principalId-de-la-identidad-administrada-del-agente-sectorial>" \
  --scope "/subscriptions/655c9df4-c812-488e-b6ae-84cafe9d5b3f/resourceGroups/rg-foundry-hosted/providers/Microsoft.CognitiveServices/accounts/hub-foundry-hosted"
```

Verificado con `az role assignment list --scope <cuenta>`:

| PrincipalId | Rol | Scope |
|---|---|---|
| `5457be5d-ba2d-486a-b064-21785ffb7da0` (identidad administrada del agente `sectorial`) | **Cognitive Services User** | Cuenta `hub-foundry-hosted` |

**Hallazgo importante:** ser `Owner`/`Contributor` de la suscripción **no incluye permisos de plano
de datos** de Cognitive Services — son dos cosas distintas (RBAC de control plane vs. data plane). Sin
este rol explícito, cualquier llamada a Document Intelligence devuelve `401 PermissionDenied` con el
mensaje `lacks the required data action
Microsoft.CognitiveServices/accounts/FormRecognizer/documentmodels:analyze/action`, incluso para un
Owner de la suscripción.

**¿Sobrevive este permiso a un redeploy (por `azd` o por el Toolkit)?** Sí. Se verificó el
`instance_identity` del agente en sus 3 versiones desplegadas (v1, v2, v3) y **el `principal_id` es
el mismo (`5457be5d-...`) en las tres** — no rota por versión. El rol asignado persiste
automáticamente mientras el redeploy actualice el mismo agente `sectorial` (no cree uno duplicado con
otro nombre, como pasó una vez antes de fijar `name: sectorial` en `azure.yaml`).

El único escenario que rompería esto es si el agente se **elimina y se recrea** — ahí Foundry
generaría una identidad nueva y habría que reasignar el rol. No se configuró ninguna automatización
para ese caso (por ejemplo, un hook `postdeploy` de `azd` que reasigne el rol de forma idempotente):
se evaluó y se descartó porque ese hook solo correría con `azd deploy`, no con el Toolkit, y hoy no es
necesario dado que la identidad es estable.

### 5.5 Variables de entorno

Declaradas en `azure.yaml` (servicio `sectorial`) y en el `.env` local:

```yaml
environmentVariables:
  - name: DOCUMENT_INTELLIGENCE_ENDPOINT
    value: ${DOCUMENT_INTELLIGENCE_ENDPOINT}
```

```
DOCUMENT_INTELLIGENCE_ENDPOINT=https://hub-foundry-hosted.cognitiveservices.azure.com
```

## 6. Resumen de permisos otorgados (tabla única)

| A quién | Rol / mecanismo | Sobre qué recurso | Para qué |
|---|---|---|---|
| Identidad administrada del agente `sectorial` | RBAC: `Cognitive Services User` | Cuenta `hub-foundry-hosted` | Llamar a Document Intelligence (`prebuilt-layout`) para leer el reporte de créditos adjunto |
| Toolbox `agent-tools` (vía conexión) | API key (no RBAC) | Servicio `srch-sectorial-22l6c` | Consultar el índice `sectorial-sharepoint-index` desde `search_conocimiento` |
| Agente `sectorial` (identidad de plataforma) | Gestionado por Foundry, sin rol adicional | Bing (Grounding) | `web_search` |
| *(Pendiente)* App registration `sectorial-sharepoint-indexer` | Graph `Files.Read.All` + `Sites.Read.All` (consentimiento admin) | SharePoint del tenant | Indexer automático hacia `sectorial-sharepoint-index` — bloqueado hasta recibir client id/secret del admin |

## 7. Limitaciones conocidas / no verificado aún

- No se ha probado end-to-end que un archivo subido desde el **Playground del portal** efectivamente
  aparezca en el working directory del contenedor — el mecanismo está documentado oficialmente pero
  no confirmado con una subida real en este proyecto.
- El agente no tiene `code_interpreter`: no puede generar los 6 gráficos que exige el prompt
  sectorial. Pendiente de agregar.
- El índice de SharePoint está vacío (ver sección 3.2).
