"""
Konfiguration des KI-Assistenten (Version 0.3.0).

Die Werte stehen in der .env (``AI_*``) und zusätzlich unter Verwaltung ›
KI-Assistent (``web.SiteSettings``, nur Superuser). Ein Wert aus der
Verwaltung hat Vorrang, ein leeres Feld bedeutet "Wert aus der .env".
Dies ist die einzige Stelle, die beides zusammenführt; Anbieter, Agent,
Rechteprüfung und Ansichten fragen ``current()``.
"""

from dataclasses import dataclass

from django.conf import settings

# Ollama läuft meist auf dem Docker-Host oder einem anderen Rechner. Im
# Container zeigt "localhost" auf den Container selbst; host.docker.internal
# erreicht den Docker-Host (docker-compose.yml: extra_hosts, Version 0.5.0).
OPENAI = "openai"
OLLAMA = "ollama"
OLLAMA_DEFAULT_URL = "http://host.docker.internal:11434/v1"


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
    if reader_provider not in (OPENAI, OLLAMA):
        return None
    same = reader_provider == provider
    reader_url = (getattr(site, "reader_base_url", "") or "").strip() or (base_url if same else "")
    if reader_provider == OLLAMA and not reader_url:
        reader_url = OLLAMA_DEFAULT_URL
    return ReaderConfig(
        provider=reader_provider, model=model, base_url=reader_url,
        api_key=getattr(site, "reader_api_key", "") or (api_key if same else ""),
        kind=getattr(site, "reader_kind", "") or "vision", timeout=timeout,
    )


def from_env():
    """Konfiguration nur aus der .env."""
    provider = settings.AI_PROVIDER
    return AIConfig(
        enabled=settings.AI_ENABLED,
        provider=provider,
        model=settings.AI_MODEL,
        base_url=settings.AI_BASE_URL or (OLLAMA_DEFAULT_URL if provider == OLLAMA else ""),
        api_key=settings.AI_API_KEY,
        timeout=settings.AI_TIMEOUT,
    )


def merge(site, env=None):
    """
    Führt die Einstellungen aus der Verwaltung (``site``: SiteSettings oder
    ein Objekt mit denselben Attributen) mit der .env zusammen.
    """
    env = env or from_env()
    provider = site.ai_provider or env.provider
    base_url = site.ai_base_url.strip() or env.base_url
    if provider == OLLAMA and not site.ai_base_url.strip() and env.provider != OLLAMA:
        # Die Adresse der .env gehört zum Anbieter der .env, nicht zu Ollama
        base_url = OLLAMA_DEFAULT_URL
    elif provider == OLLAMA and not base_url:
        base_url = OLLAMA_DEFAULT_URL
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
    """Gültige Konfiguration: Verwaltung vor .env."""
    from web.models import SiteSettings

    return merge(SiteSettings.load())


def enabled():
    return current().enabled
