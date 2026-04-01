FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# dwg2dxf pre-compiled binary (LibreDWG 0.12.5, ubuntu-22.04, x86_64)
# Built by GitHub Actions CI (.github/workflows/build-dwg2dxf.yml)
# and committed to tools/dwg2dxf. No network download at build time.
COPY tools/dwg2dxf /usr/local/bin/dwg2dxf
RUN chmod +x /usr/local/bin/dwg2dxf \
    && dwg2dxf --version \
    && echo "dwg2dxf OK"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
