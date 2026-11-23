#!/bin/sh
# Startskript des App-Containers (auch für den Worker).
#
# Version 0.9.0: Startet der Container als root (Standard), setzt das Skript
# den Besitzer der Ordner media und config auf APP_UID:APP_GID (Standard
# 1000:1000) und startet sich danach mit setpriv als dieser Benutzer neu. So
# entfällt das "chown" bei der Einrichtung; die App selbst läuft nie als root.
# Mit "user:" in der Compose-Datei (bis 0.8.0) startet es direkt als dieser
# Benutzer, dann bleiben die Ordner unverändert.
#
# Danach: ausstehende Migrationen einspielen, Übersetzungen kompilieren und
# den übergebenen Befehl starten (Standard: gunicorn aus dem Dockerfile).
set -e

if [ "$(id -u)" = "0" ]; then
    uid="${APP_UID:-1000}"
    gid="${APP_GID:-1000}"
    case "$uid$gid" in
        *[!0-9]*) echo "APP_UID und APP_GID müssen Zahlen sein." >&2; exit 1 ;;
    esac
    if [ "$uid" = "0" ]; then
        echo "APP_UID 0 (root) ist nicht erlaubt." >&2
        exit 1
    fi
    for dir in "${MEDIA_ROOT:-/app/media}" "${CONFIG_DIR:-/config}"; do
        if [ -d "$dir" ]; then
            # Nur Einträge mit anderem Besitzer ändern (schnell bei vielen Dateien)
            # (Fehler, z. B. auf Freigaben ohne chown, brechen den Start nicht ab)
            find "$dir" \( ! -user "$uid" -o ! -group "$gid" \) -exec chown "$uid:$gid" {} + \
                || echo "Hinweis: Besitzer von $dir konnte nicht vollständig geändert werden." >&2
        fi
    done
    if [ -d "${CONFIG_DIR:-/config}" ]; then
        chmod 700 "${CONFIG_DIR:-/config}" || true
    fi
    export HOME=/tmp
    exec setpriv --reuid="$uid" --regid="$gid" --clear-groups --inh-caps=-all /usr/local/bin/entrypoint.sh "$@"
fi

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
