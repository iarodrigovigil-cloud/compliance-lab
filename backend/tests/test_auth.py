# ── tests/test_auth.py ─────────────────────────────────────────────
#  COMPLIANCE LAB · Tests de autenticación
# ─────────────────────────────────────────────────────────────────

from .conftest import auth_headers, CREDENCIALES_SEED


async def test_login_admin_correcto(client):
    """El admin seed debe poder loguearse con sus credenciales correctas."""
    email, password = CREDENCIALES_SEED["admin"]
    resp = await client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200
    datos = resp.json()
    assert "access_token" in datos
    assert len(datos["access_token"]) > 20


async def test_login_supervisor_correcto(client):
    email, password = CREDENCIALES_SEED["supervisor"]
    resp = await client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200


async def test_login_officer_correcto(client):
    email, password = CREDENCIALES_SEED["officer"]
    resp = await client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200


async def test_login_password_incorrecta(client):
    """Una contraseña incorrecta debe devolver 401, no un error 500."""
    resp = await client.post(
        "/auth/login",
        data={"username": "admin@jrvlab.es", "password": "esta-password-no-es-correcta"},
    )
    assert resp.status_code == 401


async def test_login_usuario_inexistente(client):
    """Un email que no existe debe devolver 401, sin filtrar si el email existe o no."""
    resp = await client.post(
        "/auth/login",
        data={"username": "usuario-que-no-existe@jrvlab.es", "password": "cualquiera"},
    )
    assert resp.status_code == 401


async def test_login_sin_credenciales(client):
    """Un intento de login sin body debe fallar de forma controlada (422), no reventar."""
    resp = await client.post("/auth/login", data={})
    assert resp.status_code in (401, 422)
