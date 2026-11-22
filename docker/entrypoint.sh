#!/bin/sh
# Startskript des App-Containers.
# Spielt ausstehende Migrationen ein, kompiliert die Übersetzungen und
# startet danach den übergebenen Befehl (Standard: gunicorn aus dem
# Dockerfile).
set -e

echo "Migrationen werden eingespielt ..."
python manage.py migrate --noinput

# Seit 0.8.0 legt ADMIN_* keinen Administrator mehr an (Erstanlage im
# Browser); ensure_admin weist nur noch auf übrig gebliebene Variablen hin.
python manage.py ensure_admin || true

# Übersetzungen (.po) in das Laufzeitformat (.mo) übersetzen. Im Image sind
# sie schon beim Bauen kompiliert; beim Start nur, wenn der Ordner
# beschreibbar ist (Entwicklung mit eingebundenem Code).
if [ -d locale ] && [ -w locale/de/LC_MESSAGES ]; then
    echo "Übersetzungen werden kompiliert ..."
    python manage.py compilemessages --ignore "venv/*" --ignore "data/*" --verbosity 0
fi

exec "$@"
