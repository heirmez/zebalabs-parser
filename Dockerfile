FROM python:3.11-slim-bookworm

WORKDIR /app

# Runtime deps for dwg2dxf (libglib2.0-0 required by LibreDWG)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# dwg2dxf pre-compiled binary (LibreDWG 0.12.5, ubuntu-22.04, x86_64, stripped)
# Built by .github/workflows/build-dwg2dxf.yml — no network download at build time.
COPY bin/dwg2dxf /usr/local/bin/dwg2dxf
RUN chmod +x /usr/local/bin/dwg2dxf \
    && dwg2dxf --version \
    && echo "dwg2dxf OK"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
