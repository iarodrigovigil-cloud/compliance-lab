# 🚀 Compliance Lab — Guía del Aprendiz

## ¿Qué es este proyecto?
Una plataforma web que analiza documentos KYC (empresas, notarías, bancos)
usando Inteligencia Artificial. Sube un PDF, la IA lo clasifica y extrae los datos.

## Estructura del proyecto
```
compliance-lab/
  ├── backend/        ← El cerebro (Python + FastAPI)
  ├── frontend/       ← Lo que ve el usuario (React)  
  ├── ai-engine/      ← La IA que analiza documentos (LegNER)
  ├── docs/           ← Documentación
  └── scripts/        ← Scripts de ayuda
```

## Orden de instalación (hazlo una sola vez)

### 1. Python
Descarga de: https://python.org (versión 3.11 o superior)
Verifica con: `python --version`

### 2. Node.js
Descarga de: https://nodejs.org (versión 20 LTS)
Verifica con: `node --version`

### 3. Docker Desktop
Descarga de: https://docker.com/products/docker-desktop
Lo usamos para la base de datos PostgreSQL

### 4. VS Code
Descarga de: https://code.visualstudio.com
Extensiones útiles: Python, ESLint, Prettier

## Cómo arrancar el proyecto (cada vez)

### Terminal 1 — Base de datos
```bash
cd compliance-lab
docker-compose up -d
```

### Terminal 2 — Backend (la API)
```bash
cd compliance-lab/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Terminal 3 — Frontend (la web)
```bash
cd compliance-lab/frontend
npm install
npm run dev
```

Luego abre: http://localhost:3000

## Tu API key de Anthropic (Claude)
Necesitas una en: https://console.anthropic.com
Créala y ponla en el archivo .env (te lo explico más abajo)
