"""Lectura de la carpeta de SharePoint vía Microsoft Graph (client credentials)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import Config

GRAPH = "https://graph.microsoft.com/v1.0"


@dataclass(frozen=True)
class Archivo:
    id: str
    nombre: str
    url: str
    modificado: str


class ClienteSharePoint:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._drive_id: str | None = None

    async def __aenter__(self) -> "ClienteSharePoint":
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0), follow_redirects=True)
        self._token = await self._obtener_token()
        return self

    async def __aexit__(self, *_) -> None:
        await self._http.aclose()

    async def _obtener_token(self) -> str:
        respuesta = await self._http.post(
            f"https://login.microsoftonline.com/{self._config.sharepoint_tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self._config.sharepoint_client_id,
                "client_secret": self._config.sharepoint_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        respuesta.raise_for_status()
        return respuesta.json()["access_token"]

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _get(self, url: str) -> dict:
        respuesta = await self._http.get(url, headers=self._headers)
        respuesta.raise_for_status()
        return respuesta.json()

    def _partes_carpeta(self) -> tuple[str, str]:
        """Separa SHAREPOINT_FOLDER_PATH en (biblioteca, subcarpeta dentro de la biblioteca)."""
        carpeta = self._config.sharepoint_folder_path.strip("/")
        ruta_sitio = urlparse(self._config.sharepoint_site_url).path.strip("/")
        if carpeta.lower().startswith(ruta_sitio.lower()):
            carpeta = carpeta[len(ruta_sitio):].strip("/")
        biblioteca, _, subcarpeta = carpeta.partition("/")
        return biblioteca, subcarpeta

    async def _resolver_drive(self) -> str:
        if self._drive_id:
            return self._drive_id

        sitio = urlparse(self._config.sharepoint_site_url)
        datos = await self._get(f"{GRAPH}/sites/{sitio.netloc}:{sitio.path}")
        drives = (await self._get(f"{GRAPH}/sites/{datos['id']}/drives"))["value"]

        biblioteca, _ = self._partes_carpeta()
        normalizar = lambda texto: texto.lower().replace("-", "").replace(" ", "")  # noqa: E731
        for drive in drives:
            segmento = urlparse(drive["webUrl"]).path.rstrip("/").rsplit("/", 1)[-1]
            if normalizar(segmento) == normalizar(biblioteca) or normalizar(drive["name"]) == normalizar(biblioteca):
                self._drive_id = drive["id"]
                return self._drive_id

        disponibles = ", ".join(d["name"] for d in drives)
        raise RuntimeError(f"No se encontró la biblioteca '{biblioteca}'. Disponibles: {disponibles}")

    async def listar_archivos(self) -> list[Archivo]:
        drive = await self._resolver_drive()
        _, subcarpeta = self._partes_carpeta()
        raiz = f"{GRAPH}/drives/{drive}/root:/{subcarpeta}:/children" if subcarpeta else f"{GRAPH}/drives/{drive}/root/children"

        archivos: list[Archivo] = []
        pendientes = [raiz]
        while pendientes:
            url = pendientes.pop()
            while url:
                pagina = await self._get(url)
                for item in pagina["value"]:
                    if "folder" in item:
                        pendientes.append(f"{GRAPH}/drives/{drive}/items/{item['id']}/children")
                    elif self._es_soportado(item["name"]):
                        archivos.append(
                            Archivo(
                                id=item["id"],
                                nombre=item["name"],
                                url=item["webUrl"],
                                modificado=item["lastModifiedDateTime"],
                            )
                        )
                url = pagina.get("@odata.nextLink")
        return archivos

    def _es_soportado(self, nombre: str) -> bool:
        return any(nombre.lower().endswith(ext) for ext in self._config.extensiones)

    async def descargar(self, archivo_id: str) -> bytes:
        drive = await self._resolver_drive()
        respuesta = await self._http.get(
            f"{GRAPH}/drives/{drive}/items/{archivo_id}/content", headers=self._headers
        )
        respuesta.raise_for_status()
        return respuesta.content
