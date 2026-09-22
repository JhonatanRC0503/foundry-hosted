# Configuración y despliegue del agente `financiero` — errores encontrados y cómo evitarlos

Este documento es una guía práctica para **desplegar un hosted agent con `azd` sin repetir los
errores** que aparecieron al desplegar `financiero`. Está pensado para consultarse antes de
desplegar un agente nuevo (incluso en otra suscripción/tenant), porque **todos estos errores son de
configuración de `azd`/Foundry, no del código del agente**, y por lo tanto se repiten con cualquier
agente nuevo si no se sigue el orden correcto.

## Resumen de los errores encontrados (y su causa real)

| Error | Cuándo aparece | Causa real |
|---|---|---|
| `TOOLBOX_<SERVICE>_MCP_ENDPOINT is not set` | Al hacer `azd deploy <agente>` | Se intentó desplegar el agente **antes** de que su toolbox existiera. |
| `POST .../toolboxes/<nombre>/versions` → `404 WorkspaceNotFound` | Al hacer `azd deploy <toolbox>` | Bug conocido de la extensión `azure.ai.toolboxes` de `azd` (no es un problema de permisos ni de tenant). |
| `AZURE_LOCATION is not set; the Foundry project region is required for code deploy` | Al hacer `azd deploy <agente>` en un proyecto Foundry ya existente | `azd` no siempre reutiliza `AZURE_LOCATION`/`AZURE_AI_PROJECT_ID` del proyecto aunque el proyecto ya exista. |
| `424 Failed Dependency` al invocar el agente (aun con estado `active`) — causa 1 | Primera invocación de un agente **recién creado** | La identidad administrada de un agente nuevo **nace sin ningún rol RBAC**. Si usa un toolbox (`code_interpreter`, `azure_ai_search`, etc.) o Document Intelligence, falla al inicializar esos tools. |
| `424 session_not_ready` ("verify the /readiness endpoint returns HTTP 200") — causa 2 | Invocar el agente después de un deploy que terminó "Done" sin error | El contenedor **crashea al arrancar** porque falta una variable de entorno en el **azd env** (no en `azure.yaml`) — por ejemplo `AZURE_AI_MODEL_DEPLOYMENT_NAME` o `DOCUMENT_INTELLIGENCE_ENDPOINT`. `azd deploy` no valida esto porque solo empaqueta y publica; el error solo se ve en el arranque real del proceso Python. |
| Se crea un agente **duplicado** en vez de actualizar el existente | Primer `azd deploy <servicio>` sobre un agente que ya existía manualmente (ej. creado por Foundry Toolkit) | El campo `name:` del servicio en `azure.yaml` no coincidía con el nombre real del agente ya desplegado (quedó el default del sample, ej. `agent-framework-agent-basic-responses`). |

## Checklist para desplegar un agente nuevo (seguir en este orden)

1. **Antes de tocar nada, confirma el `name:` del servicio del agente en `azure.yaml`.**
   Debe coincidir EXACTAMENTE con el nombre real que quieres que tenga el agente en Foundry (o con
   uno que ya exista si quieres actualizarlo, no crear un duplicado). Si no estás seguro de si ya
   existe, revísalo con:
   ```bash
   az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
   curl -s -H "Authorization: Bearer $TOKEN" "<project_endpoint>/agents?api-version=2025-11-15-preview"
   ```

2. **Setea explícitamente estas variables en el azd env ANTES del primer deploy** (no asumas que
   `azd` las hereda del proyecto Foundry, aunque este ya exista):
   ```bash
   azd env set AZURE_AI_PROJECT_ID "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<cuenta>/projects/<proyecto>"
   azd env set AZURE_LOCATION "<region, ej. centralus>"
   azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "<nombre exacto del model deployment, ej. gpt-5-mini>"
   ```
   Y cualquier otra variable que declare el `environmentVariables:` del servicio en `azure.yaml`
   (en este agente, además: `DOCUMENT_INTELLIGENCE_ENDPOINT`). **Si el `azure.yaml` referencia
   `${ALGO}`, esa variable tiene que existir en el azd env antes de desplegar** — si no, el deploy
   "termina bien" (agente queda `active`) pero el contenedor crashea al primer arranque real y solo
   se ve al invocar (`424 session_not_ready`).

3. **Despliega primero el/los toolbox(es), nunca el agente antes.**
   ```bash
   azd deploy <nombre-del-toolbox>
   ```
   Si falla con `404 WorkspaceNotFound` en `POST .../toolboxes/<nombre>/versions` (bug conocido de
   `azd`, no de permisos), usa el workaround por REST directo:
   ```bash
   TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
   PROJECT_ENDPOINT="https://<cuenta>.services.ai.azure.com/api/projects/<proyecto>"

   # Crear la nueva versión (array `tools` completo, no un diff)
   curl -s -X POST "$PROJECT_ENDPOINT/toolboxes/<nombre-toolbox>/versions?api-version=v1" \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"description": "...", "tools": [ ... ]}'

   # Promoverla a default (los redeploys de una versión >1 NO se auto-promueven)
   curl -s -X PATCH "$PROJECT_ENDPOINT/toolboxes/<nombre-toolbox>?api-version=v1" \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"default_version": "<n>"}'

   # Reflejar el endpoint nuevo en el azd env, para que el deploy del agente lo detecte
   azd env set TOOLBOX_<SERVICE_EN_MAYUS>_MCP_ENDPOINT "$PROJECT_ENDPOINT/toolboxes/<nombre-toolbox>/versions/<n>/mcp?api-version=v1"
   azd env set TOOLBOX_<SERVICE_EN_MAYUS>_PROJECT_ENDPOINT "$PROJECT_ENDPOINT"
   ```

4. **Despliega el agente:**
   ```bash
   azd deploy <nombre-del-agente>
   ```

5. **Verifica el resultado con una invocación real, no solo con el estado `active`:**
   ```bash
   azd ai agent invoke <nombre-del-agente> "hola"
   ```
   Un `azd deploy` que termina "Done" **no garantiza que el proceso Python arrancó bien** — solo
   confirma que se publicó el paquete y se creó la versión del agente.

6. **Si la invocación falla con `424`, diagnostica ANTES de asumir que es RBAC:**
   ```bash
   azd ai agent monitor <nombre-del-agente> --tail 200
   ```
   Este comando muestra el traceback real de arranque del contenedor. Dos causas distintas dan `424`
   y se ven diferente en los logs:
   - **`session_not_ready` + traceback de Python al arrancar** (ej. `RuntimeError: Model deployment
     name is not configured`) → falta una env var en el azd env (paso 2). Corrige con `azd env set`
     y vuelve a `azd deploy <agente>`.
   - **Sin traceback de arranque, falla al llamar una tool específica** (code_interpreter,
     azure_ai_search, Document Intelligence) → falta un rol RBAC de la identidad administrada del
     agente (ver paso 7).

7. **Otorga RBAC a la identidad administrada del agente — nunca es automático.**
   Toda identidad nueva nace **sin ningún rol asignado**. Si el agente usa un toolbox con
   `code_interpreter`/`azure_ai_search` o llama a Document Intelligence, necesita como mínimo:
   ```bash
   # Obtener el principal id de la identidad del agente
   azd ai agent show <nombre-del-agente>   # campo "Instance Identity Principal ID"

   az role assignment create \
     --assignee-object-id "<principal-id>" --assignee-principal-type ServicePrincipal \
     --role "Cognitive Services User" \
     --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<cuenta>"
   ```
   Este permiso **persiste entre redeploys** del mismo agente (la identidad no cambia mientras no se
   borre y recree el agente) — solo hay que asignarlo una vez, justo después del primer deploy
   exitoso.

## Configuración específica de `financiero`

- **Servicio agente:** `financiero` (`azure.yaml`, `host: azure.ai.agent`).
- **Toolbox:** `financiero-tools` (`host: azure.ai.toolbox`), un solo tool: `code_interpreter`
  (`container: { type: auto }`). Deliberadamente sin `web_search` ni `azure_ai_search`.
- **Tools locales** (Agent Framework `@tool`, no forman parte del toolbox MCP), implementadas en
  [`procesar_documentos.py`](src/agent-framework-agent-basic-responses/procesar_documentos.py):
  - `listar_documentos_adjuntos()` — lista los archivos que el analista subió a la sesión.
  - `leer_reporte_credito(nombre_archivo)` — extrae texto y tablas de un adjunto vía Azure Document
    Intelligence (`prebuilt-layout`, `outputContentFormat=markdown`), preservando la estructura
    fila/columna de las tablas.
- **Variables de entorno requeridas en el azd env** (además de las genéricas del checklist):
  - `AZURE_AI_MODEL_DEPLOYMENT_NAME` = `gpt-5-mini`
  - `DOCUMENT_INTELLIGENCE_ENDPOINT` = `https://hub-foundry-hosted.cognitiveservices.azure.com`
- **Rol RBAC otorgado a la identidad del agente:** `Cognitive Services User` sobre la cuenta
  `hub-foundry-hosted` (necesario para `leer_reporte_credito`, y en general para que
  `code_interpreter` inicialice sin error).
