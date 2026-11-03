FROM python:3.13.13-slim AS runtime
WORKDIR /opt/fa-import
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && useradd --system --uid 10001 --create-home importer
COPY --chown=importer:importer --chmod=0644 app.py ./
USER importer
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=20 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"
CMD ["python", "app.py"]

FROM runtime AS testing
COPY --chown=importer:importer --chmod=0644 test_app.py ./
CMD ["python", "-m", "unittest", "-v", "test_app"]
