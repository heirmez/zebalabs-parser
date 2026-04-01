FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Runtime libs + wget for ODA download
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget python3 python3-pip libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install ODA File Converter
# Build this image LOCALLY (not on Railway) — download.opendesign.com is blocked by cloud egress
# If the auto-download fails, place odafc.deb manually in this directory and rebuild:
#   docker build --build-arg ODA_LOCAL=1 -t oda-converter .
ARG ODA_LOCAL=0
COPY ./odafc.deb* /tmp/
RUN apt-get update \
    && if [ "$ODA_LOCAL" = "1" ] && [ -f /tmp/odafc.deb ]; then \
         echo "Installing ODA from local file"; \
         apt-get install -y /tmp/odafc.deb; \
       else \
         wget -q "https://download.opendesign.com/guestfiles/Demo/ODAFileConverter_QT5_lnxX64_8.3dll_25.5.deb" \
              -O /tmp/odafc.deb \
         || wget -q "https://download.opendesign.com/guestfiles/Demo/ODAFileConverter_QT5_lnxX64_8.3dll_24.12.deb" \
              -O /tmp/odafc.deb; \
         apt-get install -y /tmp/odafc.deb; \
       fi \
    && rm -f /tmp/odafc.deb \
    && rm -rf /var/lib/apt/lists/* \
    && test -f /usr/bin/ODAFileConverter \
    && echo "ODA File Converter installed at /usr/bin/ODAFileConverter"

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY main.py .

EXPOSE 8001
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
