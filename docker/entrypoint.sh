#!/bin/sh
# Startskript des App-Containers.
# Spielt ausstehende Migrationen ein, kompiliert die Übersetzungen und
# startet danach den übergebenen Befehl (Standard: gunicorn aus dem
# Dockerfile).
set -e

echo "Migrationen werden eingespielt ..."
python manage.py migrate --noinput

# Ersten Administrator aus ADMIN_USERNAME und ADMIN_PASSWORD (.env) anlegen,
# aber nur, wenn noch keiner existiert. Ein Fehler hier bricht den Start nicht ab.
python manage.py ensure_admin || echo "Der Administrator konnte nicht angelegt werden (siehe oben)."

# Übersetzungen (.po) in das Laufzeitformat (.mo) übersetzen. Im Image sind
# sie schon beim Bauen kompiliert; beim Start nur, wenn der Ordner
# beschreibbar ist (Entwicklung mit eingebundenem Code).
if [ -d locale ] && [ -w locale/de/LC_MESSAGES ]; then
    echo "Übersetzungen werden kompiliert ..."
    python manage.py compilemessages --ignore "venv/*" --ignore "data/*" --verbosity 0
fi

exec "$@"
