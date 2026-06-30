FROM python:3.13-slim

# Dependencias del sistema: Tesseract OCR + Poppler (para pdf2image) + librerías de compilación
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/* \
    && which tesseract \
    && tesseract --version

WORKDIR /app

# Copiar solo requirements primero para aprovechar la cache de Docker
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del backend
COPY backend/ .

# Puerto que Railway inyecta dinámicamente
ENV PORT=8000
EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
