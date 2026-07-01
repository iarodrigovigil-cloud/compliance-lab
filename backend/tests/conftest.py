# ── tests/conftest.py ──────────────────────────────────────────────
#  COMPLIANCE LAB · Fixtures compartidas para tests automatizados
#
#  IMPORTANTE: estos tests corren contra tu entorno LOCAL
#  (docker-compose con Postgres+Redis + uvicorn arrancado a mano).
#  NUNCA apuntar esto a producción.
#
#  Antes de correr los tests:
#    1. docker-compose up -d          (Postgres + Redis)
#    2. uvicorn app.main:app --reload (en otra terminal, puerto 8000)
#    3. pytest -v                     (desde la carpeta backend/)
#
#  Requiere que existan los 3 usuarios seed en la BD local:
#    admin@jrvlab.es / supervisor@jrvlab.es / officer@jrvlab.es
#    (contraseña: admin2026)
# ─────────────────────────────────────────────────────────────────

import os
import pytest
import httpx

BASE_URL = os.environ.get("COMPLIANCE_LAB_TEST_URL", "http://localhost:8000")

CREDENCIALES_SEED = {
    "admin":      ("admin@jrvlab.es", "admin2026"),
    "supervisor": ("supervisor@jrvlab.es", "admin2026"),
    "officer":    ("officer@jrvlab.es", "admin2026"),
}


@pytest.fixture
async def client():
    """Cliente HTTP async apuntando al servidor local."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as c:
        yield c


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    """Hace login y devuelve el token JWT. Falla el test si el login no funciona."""
    resp = await client.post(
        "/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200, (
        f"Login falló para {email}: {resp.status_code} {resp.text}\n"
        f"¿Está el servidor local corriendo en {BASE_URL}? "
        f"¿Existen los usuarios seed en la BD local?"
    )
    return resp.json()["access_token"]


@pytest.fixture
async def token_admin(client):
    email, password = CREDENCIALES_SEED["admin"]
    return await _login(client, email, password)


@pytest.fixture
async def token_supervisor(client):
    email, password = CREDENCIALES_SEED["supervisor"]
    return await _login(client, email, password)


@pytest.fixture
async def token_officer(client):
    email, password = CREDENCIALES_SEED["officer"]
    return await _login(client, email, password)


def auth_headers(token: str) -> dict:
    """Helper para construir el header Authorization."""
    return {"Authorization": f"Bearer {token}"}
