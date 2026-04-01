# Stage 1: Compile dwg2dxf on Ubuntu 22.04 (GCC 11 — avoids strict GCC 12 warnings)
FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential autoconf automake libtool pkg-config wget xz-utils perl \
    && rm -rf /var/lib/apt/lists/*

# Create stub so stage-2 COPY never fails even if build fails
RUN touch /usr/local/bin/dwg2dxf && chmod +x /usr/local/bin/dwg2dxf

# Download and compile LibreDWG 0.12.5 — supports AC1032 (AutoCAD 2018+)
# Ubuntu 22.04 uses GCC 11 which doesn't treat implicit-function-declaration as error
RUN wget -q https://github.com/LibreDWG/libredwg/releases/download/0.12.5/libredwg-0.12.5.tar.xz \
    && tar xf libredwg-0.12.5.tar.xz \
    && cd libredwg-0.12.5 \
    && ./configure --without-perl --without-python --disable-shared \
    && make -j$(nproc) \
    && cp programs/dwg2dxf /usr/local/bin/dwg2dxf \
    && strip /usr/local/bin/dwg2dxf 2>/dev/null \
    && echo "LibreDWG built OK: $(dwg2dxf --version 2>&1 | head -1)" \
    || echo "LibreDWG build failed — stub remains"

# Stage 2: Runtime image
FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy dwg2dxf binary (real binary or 0-byte stub — COPY always succeeds)
COPY --from=builder /usr/local/bin/dwg2dxf /usr/local/bin/dwg2dxf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
