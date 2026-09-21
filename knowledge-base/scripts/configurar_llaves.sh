#!/usr/bin/env bash
# Rellena AZURE_AI_KEY y SEARCH_API_KEY en knowledge-base/.env desde Azure.
set -euo pipefail

RG="${RG:-rg-foundry-hosted}"
CUENTA_AI="${CUENTA_AI:-hub-foundry-hosted}"
SEARCH="${SEARCH:-srch-sectorial-22l6c}"
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"

llave_ai=$(az cognitiveservices account keys list --name "$CUENTA_AI" --resource-group "$RG" --query key1 -o tsv)
llave_search=$(az search admin-key show --service-name "$SEARCH" --resource-group "$RG" --query primaryKey -o tsv)

python3 - "$ENV_FILE" "$llave_ai" "$llave_search" <<'PY'
import sys
from pathlib import Path

ruta, llave_ai, llave_search = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
valores = {"AZURE_AI_KEY": llave_ai, "SEARCH_API_KEY": llave_search}
lineas = ruta.read_text(encoding="utf-8").splitlines()
for i, linea in enumerate(lineas):
    clave = linea.split("=", 1)[0]
    if clave in valores:
        lineas[i] = f"{clave}={valores.pop(clave)}"
lineas += [f"{c}={v}" for c, v in valores.items()]
ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
PY

echo "Llaves escritas en $ENV_FILE"
