FROM python:3.11-slim-bullseye

WORKDIR /app

# libredwg-utils (provides dwg2dxf) is in Debian 11 Bullseye main repos
RUN apt-get update && apt-get install -y --no-install-recommends \
    libredwg-utils \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
