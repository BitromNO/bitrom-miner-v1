# Build stage: compile cpuminer-multi from source
FROM debian:bookworm AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential automake autoconf libtool \
    pkg-config libcurl4-openssl-dev libssl-dev libjansson-dev libgmp-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/tpruvot/cpuminer-multi.git /build/cpuminer-multi
WORKDIR /build/cpuminer-multi
RUN ./autogen.sh \
    && ./configure --with-crypto --with-curl \
    && make -j"$(nproc)"

# Runtime stage: slim Debian with python3 + cpuminer runtime libs
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BITROM_BINARY=/usr/local/bin/cpuminer \
    BITROM_CONFIG_DIR=/app/data/config \
    BITROM_CACHE_DIR=/app/data/cache
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 libcurl4 libssl3 libgmp10 libjansson4 zlib1g ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/cpuminer-multi/cpuminer /usr/local/bin/cpuminer
WORKDIR /app
COPY bitrom.py bitrom/ README.md /app/
USER 1000:1000
EXPOSE 8080
CMD ["python3", "bitrom.py", "--headless", "--web", "--web-port", "8080"]