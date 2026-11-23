"""
Konfiguration des KI-Assistenten (Version 0.3.0).

Die Werte stehen unter Verwaltung › KI-Assistent (``web.SiteSettings``, nur
Superuser); ein leeres Feld bedeutet "Standardwert" (``defaults``). Die
früheren Variablen ``AI_ENABLED``, ``AI_PROVIDER``, ``AI_MODEL``,
``AI_BASE_URL`` und ``AI_API_KEY`` übernimmt ``web.env_import`` seit 0.9.0
einmalig, danach werden sie nicht mehr gelesen. ``AI_TIMEOUT`` bleibt eine
Variable (erweitert). Anbieter, Agent, Rechteprüfung und Ansichten fragen
``current()``.
"""

from dataclasses import dataclass

from django.conf import settings

# Ollama läuft meist auf dem Docker-Host oder einem anderen Rechner. Im
# Container zeigt "localhost" auf den Container selbst; host.docker.internal
# erreicht den Docker-Host (docker-compose.yml: extra_hosts, Version 0.5.0).
OPENAI = "openai"
OLLAMA = "ollama"
OLLAMA_DEFAULT_URL = "http://host.docker.internal:11434/v1"
# Version 0.8.0: Mistral (mistral.ai) über seine OpenAI-kompatible Schnittstelle
MISTRAL = "mistral"
MISTRAL_DEFAULT_URL = "https://api.mistral.ai/v1"
# Anbieter mit fester Standardadresse; ohne eigene Adresse gilt sie
DEFAULT_URLS = {OLLAMA: OLLAMA_DEFAULT_URL, MISTRAL: MISTRAL_DEFAULT_URL}
# Anbieter mit OpenAI-Schnittstelle (auch für die Dokument-KI)
OPENAI_COMPATIBLE = (OPENAI, OLLAMA, MISTRAL)


@dataclass(frozen=True)
class ReaderConfig:
    """
    Dokument-KI (Version 0.6.0): ein Modell mit OpenAI-Schnittstelle, das
    Seiten von Scans und Fotos als Bild bekommt und den Text zurückgibt
    (assistant.vision). Läuft unabhängig vom Hauptmodell, z. B. mit
    demselben IONOS-Schlüssel, aber einem Bild- oder OCR-Modell.
    """
    provider: str
    model: str
    base_url: str
    api_key: str
    # "vision": Bild mit Anweisung; "ocr": nur das Bild (z. B. LightOnOCR)
    kind: str = "vision"
    timeout: int = 120
    max_retries: int = 1


@dataclass(frozen=True)
class AIConfig:
    enabled: bool
    provider: str
    model: str
    base_url: str
    api_key: str
    # Eigener allgemeiner Teil des System-Prompts, leer = Standard
    prompt: str = ""
    timeout: int = 120
    max_retries: int = 2
    # Dokument-KI für Scans und Fotos, None = nur lokale Texterkennung
    reader: "ReaderConfig | None" = None


def merge_reader(site, provider, base_url, api_key, timeout=120):
    """
    Dokument-KI aus der Verwaltung. Ohne Modell aus. Anbieter, Adresse und
    Schlüssel, die leer sind, gelten wie beim Hauptmodell, aber nur, wenn
    beide denselben Anbieter nutzen (ein Schlüssel passt nicht zu einem
    anderen Anbieter). Nur Anbieter mit OpenAI-Schnittstelle.
    """
    model = (getattr(site, "reader_model", "") or "").strip()
    if not model:
        return None
    reader_provider = getattr(site, "reader_provider", "") or provider
    if reader_provider not in OPENAI_COMPATIBLE:
        return None
    same = reader_provider == provider
    reader_url = (getattr(site, "reader_base_url", "") or "").strip() or (base_url if same else "")
    if not reader_url:
        reader_url = DEFAULT_URLS.get(reader_provider, "")
    return ReaderConfig(
        provider=reader_provider, model=model, base_url=reader_url,
        api_key=getattr(site, "reader_api_key", "") or (api_key if same else ""),
        kind=getattr(site, "reader_kind", "") or "vision", timeout=timeout,
    )


def defaults():
    """Standardwerte für leere Felder (seit 0.9.0 nicht mehr aus der .env)."""
    return AIConfig(
        enabled=False,
        provider=OPENAI,
        model="",
        base_url="",
        api_key="",
        timeout=settings.AI_TIMEOUT,
    )


def merge(site, env=None):
    """
    Einstellungen aus der Verwaltung (``site``: SiteSettings oder ein Objekt
    mit denselben Attributen), leere Felder mit den Standardwerten.
    """
    env = env or defaults()
    provider = site.ai_provider or env.provider
    base_url = site.ai_base_url.strip() or DEFAULT_URLS.get(provider, "")
    api_key = site.ai_api_key or env.api_key
    return AIConfig(
        enabled={"on": True, "off": False}.get(site.ai_enabled, env.enabled),
        provider=provider,
        model=site.ai_model.strip() or env.model,
        base_url=base_url,
        api_key=api_key,
        prompt=site.ai_system_prompt.strip(),
        timeout=env.timeout,
        reader=merge_reader(site, provider, base_url, api_key, env.timeout),
    )


def current():
    """Gültige Konfiguration aus der Verwaltung."""
    from web.models import SiteSettings

    return merge(SiteSettings.load())


def enabled():
    return current().enabled
