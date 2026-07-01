# ── tests/test_roles.py ────────────────────────────────────────────
#  COMPLIANCE LAB · Tests de control de acceso por rol
#
#  Verifican que la separación de permisos entre admin / supervisor /
#  aml_officer se mantiene intacta tras cada cambio de código.
# ─────────────────────────────────────────────────────────────────

from .conftest import auth_headers


async def test_auth_me_devuelve_rol_correcto(client, token_admin, token_supervisor, token_officer):
    """Cada usuario debe ver su propio rol reflejado en /auth/me."""
    resp_admin = await client.get("/auth/me", headers=auth_headers(token_admin))
    assert resp_admin.status_code == 200
    assert resp_admin.json()["rol"] == "admin"

    resp_supervisor = await client.get("/auth/me", headers=auth_headers(token_supervisor))
    assert resp_supervisor.status_code == 200
    assert resp_supervisor.json()["rol"] == "supervisor"

    resp_officer = await client.get("/auth/me", headers=auth_headers(token_officer))
    assert resp_officer.status_code == 200
    assert resp_officer.json()["rol"] == "aml_officer"


async def test_admin_puede_ejecutar_agente3(client, token_admin):
    """El admin debe poder ejecutar el ciclo de Agente 3 (rescreening)."""
    resp = await client.post("/api/agente3/ejecutar", headers=auth_headers(token_admin))
    assert resp.status_code == 200
    datos = resp.json()
    assert "alertas_generadas" in datos


async def test_supervisor_no_puede_ejecutar_agente3(client, token_supervisor):
    """Agente 3 es exclusivo de admin — un supervisor debe recibir 403, no 500 ni 200."""
    resp = await client.post("/api/agente3/ejecutar", headers=auth_headers(token_supervisor))
    assert resp.status_code == 403


async def test_officer_no_puede_ejecutar_agente3(client, token_officer):
    """Agente 3 es exclusivo de admin — un AML officer debe recibir 403."""
    resp = await client.post("/api/agente3/ejecutar", headers=auth_headers(token_officer))
    assert resp.status_code == 403


async def test_sin_token_devuelve_401(client):
    """Cualquier endpoint protegido sin token de autenticación debe devolver 401, no 500."""
    resp = await client.post("/api/agente3/ejecutar")
    assert resp.status_code == 401


async def test_token_invalido_devuelve_401(client):
    """Un token con formato incorrecto o falsificado no debe dar acceso."""
    resp = await client.get("/auth/me", headers=auth_headers("token-falso-inventado"))
    assert resp.status_code == 401
