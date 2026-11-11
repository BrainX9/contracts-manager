#!/bin/sh
# Sicherung der Datenbank mit pg_dump (Custom-Format, komprimiert).
#
# Aufruf aus beliebigem Verzeichnis, z. B. über die Aufgabenplanung der
# Synology (als root):
#   sh /volume1/docker/contracts-manager/scripts/backup.sh
#
# Nur für Installationen mit Repository (git clone). Ohne Repository siehe
# docs/operations.md, Abschnitt "Create a backup" (Einzeiler mit docker exec).
#
# Ziel:          BACKUP_DIR, Standard ${DATA_PATH}/backups (aus .env),
#                ohne DATA_PATH der Ordner backups neben docker-compose.yml
# Aufbewahrung:  BACKUP_RETENTION_DAYS, Standard 30 Tage
# Beide Werte können in .env oder als Umgebungsvariable gesetzt werden.
#
# Wiederherstellung: siehe docs/operations.md, Abschnitt "Restore".

set -eu

# Die Aufgabenplanung der Synology hat einen sehr kurzen PATH
PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# In das Projektverzeichnis wechseln (dort liegen .env und docker-compose.yml)
cd "$(dirname "$0")/.."

# Einzelnen Wert aus .env lesen, ohne die Datei als Skript auszuführen
env_value() {
    sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1
}

DATA_PATH="${DATA_PATH:-$(env_value DATA_PATH)}"
DATA_PATH="${DATA_PATH:-.}"
BACKUP_DIR="${BACKUP_DIR:-$(env_value BACKUP_DIR)}"
BACKUP_DIR="${BACKUP_DIR:-$DATA_PATH/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-$(env_value BACKUP_RETENTION_DAYS)}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

# docker compose (Plugin) oder docker-compose (ältere Synology-Pakete)
if docker compose version >/dev/null 2>&1; then
    compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
    compose() { docker-compose "$@"; }
else
    echo "Fehler: weder 'docker compose' noch 'docker-compose' gefunden." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

timestamp="$(date +%Y-%m-%d_%H%M%S)"
target="$BACKUP_DIR/contracts-$timestamp.dump"
partial="$target.partial"

echo "Sicherung nach $target ..."

# Zugangsdaten kommen aus der Umgebung des Datenbank-Containers.
# Erst in eine Teildatei schreiben, damit keine halben Sicherungen liegen bleiben.
if ! compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$partial"; then
    rm -f "$partial"
    echo "Fehler: pg_dump ist fehlgeschlagen." >&2
    exit 1
fi

if [ ! -s "$partial" ]; then
    rm -f "$partial"
    echo "Fehler: Die Sicherung ist leer." >&2
    exit 1
fi

mv "$partial" "$target"
chmod 600 "$target"
echo "Fertig: $(du -h "$target" | cut -f1)"

# Alte Sicherungen entfernen
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'contracts-*.dump' \
    -mtime +"$BACKUP_RETENTION_DAYS" -print -exec rm -f {} \;
