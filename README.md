# Compliance Lab
**JRV Lab S.L.** · Sevilla · 2026 · *"Compliance sin fricción."*

## Arranque rápido (Windows)

### Paso 1 — Base de datos
Abre PowerShell en esta carpeta y ejecuta:
```
docker-compose up -d
```

### Paso 2 — Backend
Abre un segundo PowerShell en la carpeta `backend/` y ejecuta:
```
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Paso 3 — Abrir en el navegador
- http://localhost:8000       → API funcionando
- http://localhost:8000/docs  → Swagger (prueba todas las rutas)

## Estructura
```
compliance-lab/
├── docker-compose.yml   ← Arranca PostgreSQL + Redis
├── init.sql             ← Crea las tablas KYC automáticamente
├── backend/             ← API Python FastAPI
│   └── app/main.py      ← Código principal
├── frontend/            ← React (Paso 5)
├── ai-engine/           ← LegNER clasificador (Paso 4)
└── scripts/             ← Utilidades
```

## Stack
- Backend: Python 3.11 + FastAPI + PostgreSQL
- Frontend: React 18 + TypeScript (próximo paso)
- IA: Claude API (LegNER) — clasificación KYC
- Infraestructura: Docker + Redis
