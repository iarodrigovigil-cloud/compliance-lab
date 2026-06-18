"""
Compliance Lab — Configuración central
Lee las variables del archivo .env y las pone disponibles en todo el backend
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """
    Todas las configuraciones del sistema.
    Pydantic las lee automáticamente del archivo .env
    """
    
    # Información de la aplicación
    app_name: str = "Compliance Lab"
    app_version: str = "0.1.0-poc"
    environment: str = "development"
    debug: bool = True
    
    # Base de datos
    database_url: str
    
    # IA — Claude (LegNER)
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-6"
    
    # Seguridad
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # Archivos
    storage_path: str = "./storage/documentos"
    max_file_size_mb: int = 50
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """
    Devuelve la configuración (en caché para no leerla mil veces)
    Uso: from app.core.config import get_settings; settings = get_settings()
    """
    return Settings()
