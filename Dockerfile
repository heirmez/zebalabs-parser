# Stage 1: Build LibreDWG from source
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make autoconf automake libtool pkg-config git wget \
    && rm -rf /var/lib/apt/lists/*

# Download and build LibreDWG (provides dwg2dxf)
RUN wget -q https://github.com/LibreDWG/libredwg/releases/download/0.12.5/libredwg-0.12.5.tar.xz \
    && tar xf libredwg-0.12.5.tar.xz \
    && cd libredwg-0.12.5 \
    && ./configure \
    && make -j$(nproc) \
    && cp programs/dwg2dxf /usr/local/bin/dwg2dxf \
    && chmod +x /usr/local/bin/dwg2dxf \
    && strip /usr/local/bin/dwg2dxf 2>/dev/null || true

# Stage 2: Runtime image
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy dwg2dxf binary from builder
COPY --from=builder /usr/local/bin/dwg2dxf /usr/local/bin/dwg2dxf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
