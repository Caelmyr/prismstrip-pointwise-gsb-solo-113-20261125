ARG PRISMSTRIP_DEPS_IMAGE=python:3.11-slim
FROM ${PRISMSTRIP_DEPS_IMAGE} AS dependencies
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PRISMSTRIP_DATA=/var/lib/prismstrip \
    PORT=8000
WORKDIR /srv/prismstrip
COPY --from=dependencies /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY prismstrip prismstrip
COPY image_lab image_lab
COPY static static
COPY templates templates
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=6 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/prismstrip/health').read()"]
CMD ["python", "-m", "prismstrip"]
