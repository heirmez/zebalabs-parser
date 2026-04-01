FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    wget \
    ca-certificates \
    libglib2.0-0 \
    libxrender1 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Download and install ODA File Converter
RUN wget -q https://download.opendesign.com/guestfiles/ODAFileConverter_QT5_lnxX64_8.3dll_24.12.deb \
    && apt-get update \
    && apt-get install -y ./ODAFileConverter_QT5_lnxX64_8.3dll_24.12.deb || true \
    && rm -f ODAFileConverter_QT5_lnxX64_8.3dll_24.12.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY main.py .

RUN apt-get update && apt-get install -y python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install fastapi uvicorn python-multipart

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
