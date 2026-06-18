@echo off
echo.
echo ====================================================
echo   COMPLIANCE LAB - Arranque desarrollo (Windows)
echo   JRV Lab S.L. 2026
echo ====================================================
echo.
echo [1/2] Arrancando base de datos...
docker-compose up -d
echo.
echo [2/2] Listo. Ahora en un terminal nuevo ejecuta:
echo   cd backend
echo   pip install -r requirements.txt
echo   uvicorn app.main:app --reload
echo.
echo Abre en el navegador:
echo   http://localhost:8000       (API)
echo   http://localhost:8000/docs  (Swagger)
echo.
pause
