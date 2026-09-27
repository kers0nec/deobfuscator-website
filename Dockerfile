# kers0ne deobfuscator
#
# Stage 1 builds the patched Luau runtime (luau + luau-ast) from source: the
# vector metatable is left writable so the sandbox can install Roblox's Vector3
# members. Stage 2 is the service: Python, the vendored engines, those binaries,
# and Lune when it can be fetched.
#
#   docker build -t kers0ne-deobfuscator .
#   docker run --rm -p 8000:8000 kers0ne-deobfuscator

FROM debian:bookworm-slim AS luau
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build git python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY tools/build_luau.py tools/build_luau.py
# --portable: no -march=native, so the image runs on any x86-64/arm64 host.
# The script derives its output directory from its own location, so the binaries
# land in /src/engine/cadmio/deobf/bin for the stage below to copy.
RUN python3 tools/build_luau.py --portable

FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KERS_HOST=0.0.0.0 \
    PORT=8000 \
    KERS_SITE_NAME="kers0ne website"
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY web/ web/
COPY tools/ tools/
COPY engine/ engine/
COPY run.py README.md LICENSE CREDITS.md ./

# patched Luau binaries from the build stage
COPY --from=luau /src/engine/cadmio/deobf/bin/luau \
                 /src/engine/cadmio/deobf/bin/luau-ast \
                 engine/cadmio/deobf/bin/

# Lune is only needed for full Luraph v14.x devirtualization. Its absence must
# not fail the build: without it v14.x jobs still unpack and trace, and
# /api/health says exactly what is missing.
RUN bash tools/install_lune.sh || \
    echo "[i] Lune unavailable at build time; Luraph v14.x will unpack + trace only"

# the traced script is untrusted: run as a non-root user with no write access
# to the engines
RUN useradd --create-home --uid 10001 deobf \
    && mkdir -p /app/var/jobs \
    && chown -R deobf:deobf /app/var \
    && chmod -R a+rX /app/engine /app/server /app/web
USER deobf

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).status==200 else 1)"

CMD ["python", "run.py"]
