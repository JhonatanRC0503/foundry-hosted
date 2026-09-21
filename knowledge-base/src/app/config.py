from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parents[2]


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=RAIZ / ".env", extra="ignore")

    sharepoint_tenant_id: str
    sharepoint_client_id: str
    sharepoint_client_secret: str
    sharepoint_site_url: str
    sharepoint_folder_path: str
    sharepoint_extensiones: str = ".pdf,.docx,.xlsx"

    azure_ai_key: str
    document_intelligence_endpoint: str
    azure_openai_endpoint: str
    embedding_deployment: str = "text-embedding-3-large"
    embedding_dimensions: int = 3072

    search_endpoint: str
    search_index_name: str
    search_api_key: str

    chunk_max_tokens: int = 1200
    chunk_overlap_tokens: int = 200

    @property
    def extensiones(self) -> set[str]:
        return {e.strip().lower() for e in self.sharepoint_extensiones.split(",") if e.strip()}

    @property
    def ruta_estado(self) -> Path:
        return RAIZ / "estado.json"


config = Config()
