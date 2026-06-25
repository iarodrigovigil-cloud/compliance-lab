# ── auth.py ──────────────────────────────────────────────
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import bcrypt
import os

# ── Configuración ──────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "compliance-lab-secret-2026-jrvlab")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 horas

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ── Passwords ──────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verificar_password(password: str, hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), hash.encode())


# ── Tokens JWT ─────────────────────────────────────────────
def crear_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
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


# ── Dependencia FastAPI ────────────────────────────────────
async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decodificar_token(token)
    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail="Token sin usuario")
    return {"email": email, "rol": payload.get("rol"), "id": payload.get("id")}

async def require_aml_officer(user=Depends(get_current_user)):
    if user["rol"] not in ["aml_officer", "admin"]:
        raise HTTPException(status_code=403, detail="Se requiere rol AML Officer")
    return user
