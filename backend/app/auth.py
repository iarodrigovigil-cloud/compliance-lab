# ── auth.py ─────────────────────────────────────────────────────────
#  COMPLIANCE LAB · Autenticación JWT con multi-tenancy Modelo C
#  JRV Lab S.L. · 2026
# ─────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import bcrypt
import os

# ── Configuración ────────────────────────────────────────────────────
SECRET_KEY  = os.getenv("JWT_SECRET_KEY", "compliance-lab-secret-2026-jrvlab")
ALGORITHM   = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 horas

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Roles válidos y su jerarquía (mayor número = más privilegios)
ROLES = {
    "aml_officer": 1,
    "supervisor":  2,
    "admin":       3,
}

# ── Passwords ────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verificar_password(password: str, hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hash.encode())
    except Exception:
        return False

# ── Tokens JWT ───────────────────────────────────────────────────────
def crear_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    """
    Crea un JWT con el contexto completo del usuario:
      sub              → email
      id               → usuario UUID
      rol              → admin | supervisor | aml_officer
      organizacion_id  → UUID del tenant
      org_nombre       → nombre de la organización (para mostrar en UI)
      org_tipo         → tipo de sujeto obligado
    """
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=expires_minutes)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decodificar_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"}
        )

# ── Dependencia principal FastAPI ────────────────────────────────────
async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Extrae y valida el usuario del JWT.
    Devuelve dict con: email, id, rol, organizacion_id, org_nombre, org_tipo
    """
    payload = decodificar_token(token)
    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail="Token sin usuario")
    org_id = payload.get("organizacion_id")
    if not org_id:
        raise HTTPException(status_code=401, detail="Token sin organización")
    return {
        "email":           email,
        "id":              payload.get("id"),
        "rol":             payload.get("rol"),
        "organizacion_id": org_id,
        "org_nombre":      payload.get("org_nombre", ""),
        "org_tipo":        payload.get("org_tipo", ""),
    }

# ── Guards por rol ───────────────────────────────────────────────────
async def require_aml_officer(user=Depends(get_current_user)) -> dict:
    """Permite aml_officer, supervisor y admin."""
    if ROLES.get(user["rol"], 0) < ROLES["aml_officer"]:
        raise HTTPException(status_code=403, detail="Se requiere rol AML Officer o superior")
    return user

async def require_supervisor(user=Depends(get_current_user)) -> dict:
    """Permite supervisor y admin."""
    if ROLES.get(user["rol"], 0) < ROLES["supervisor"]:
        raise HTTPException(
            status_code=403,
            detail="Acción restringida a Supervisor o Admin"
        )
    return user

async def require_admin(user=Depends(get_current_user)) -> dict:
    """Solo admin."""
    if user["rol"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Acción restringida a Administrador"
        )
    return user

# ── Helper: inyectar tenant en conexión PostgreSQL ───────────────────
async def set_tenant(conn, organizacion_id: str):
    """
    Establece el contexto de tenant en la conexión PostgreSQL
    para que las políticas RLS filtren automáticamente por organización.
    Llamar al inicio de cada operación de BD que requiera aislamiento.
    """
    await conn.execute(
        f"SET app.current_org_id = '{organizacion_id}'"
    )
