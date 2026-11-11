# Dockerfile für Contracts Manager

FROM python:3.12-slim

# Umgebungsvariablen. GUNICORN_CMD_ARGS enthält die Standardoptionen
# für gunicorn und lässt sich in .env überschreiben.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GUNICORN_CMD_ARGS="--bind 0.0.0.0:8000 --workers 2 --timeout 60 --no-control-socket"

# Arbeitsverzeichnis
WORKDIR /app

# gettext für die Übersetzungen (makemessages, compilemessages);
# tesseract (Deutsch, Englisch) und poppler (pdftoppm) für die lokale
# Texterkennung in Scans und Fotos (KI-Assistent, Version 0.8b)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
        tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Abhängigkeiten installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Startskript außerhalb von /app, damit es auch mit eingebundenem Code
# (Entwicklung) erhalten bleibt
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Anwendung kopieren
COPY . .

# Statische Dateien für whitenoise einsammeln und Übersetzungen kompilieren.
# Beim Bauen gibt es weder Secrets noch Datenbank, daher Platzhalter nur für
# diese Befehle.
RUN SECRET_KEY=build-only DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    sh -c "python manage.py collectstatic --noinput && python manage.py compilemessages --verbosity 0"

# Die App läuft nicht als root. Benutzer und Gruppe lassen sich in
# docker-compose.yml über APP_UID/APP_GID an den Besitzer von
# DATA_PATH/media anpassen; der Code im Image ist nur lesbar.
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
CMD ["gunicorn", "config.wsgi"]
