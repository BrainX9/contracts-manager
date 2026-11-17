"""
Dokument-KI: Scans und Fotos mit einem Bild- oder OCR-Modell lesen
(Version 0.6.0).

Die lokale Texterkennung (Tesseract) bleibt der Standard. Ist unter
Verwaltung › KI-Einstellungen eine Dokument-KI eingerichtet, liest sie
stattdessen Dateien ohne Text (Scans, Fotos): Die Seiten werden als Bilder
an ein Modell mit OpenAI-Schnittstelle geschickt (IONOS AI Model Hub,
OpenAI, Ollama) und kommen als Text zurück. Der Text wird wie jeder
erkannte Text am Anhang gespeichert; der Assistent liest ihn mit
``read_attachment``. Bilder in Werkzeug-Ergebnissen erlaubt die OpenAI-
Schnittstelle nicht, deshalb der Weg über den gespeicherten Text.

Eigenheiten der Anbieter:

- IONOS und Ollama nehmen keine PDF-Dateien an: Die Seiten werden mit
  poppler (pdftoppm, 200 dpi) in Bilder umgewandelt.
- IONOS erlaubt höchstens 12 MiB je Bild und 16 MiB je Anfrage; Ollama nimmt
  Bilder nur als Base64 (keine Adressen). Jede Seite geht deshalb als eigene
  Anfrage, auf 2400 Pixel Kantenlänge verkleinert und als JPEG.
- Ein Modell ohne Bildeingabe antwortet bei IONOS mit Fehler 400.
"""

import base64
import logging
import os
import subprocess
import tempfile
import time
from io import BytesIO

from .providers import ProviderError, _error_text

logger = logging.getLogger(__name__)

# Seiten je Datei und Gesamtzeit; der Worker bricht Aufgaben nach 300 s ab
# (Q_CLUSTER timeout), die Antwort des Assistenten folgt erst danach
MAX_PAGES = 8
TIME_BUDGET = 240
PDF_DPI = 200
PDF_TIMEOUT = 120
MAX_EDGE = 2400
JPEG_QUALITY = 85
MAX_TOKENS = 4096

PROMPT = (
    "Transcribe all text in this image of a document exactly as written, in reading order. "
    "Use Markdown tables for tables. Reply with the text only, without comments. "
    "If the image contains no text, reply with nothing."
)


def prepare_image(source):
    """
    Bild für die Anfrage: Drehung aus den EXIF-Daten übernehmen, Transparenz
    auf Weiß, höchstens MAX_EDGE Pixel, als JPEG. ``source`` ist ein Pfad
    oder Bytes.
    """
    from PIL import Image, ImageOps

    with Image.open(BytesIO(source) if isinstance(source, bytes) else source) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        image.thumbnail((MAX_EDGE, MAX_EDGE))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def pdf_page_images(data):
    """Seiten eines PDFs als JPEG-Bilder, höchstens MAX_PAGES."""
    with tempfile.TemporaryDirectory() as folder:
        source = os.path.join(folder, "input.pdf")
        with open(source, "wb") as file:
            file.write(data)
        subprocess.run(
            ["pdftoppm", "-r", str(PDF_DPI), "-jpeg", "-l", str(MAX_PAGES), source, os.path.join(folder, "page")],
            check=True, capture_output=True, timeout=PDF_TIMEOUT,
        )
        names = sorted(name for name in os.listdir(folder) if name.startswith("page") and name.endswith(".jpg"))
        images = []
        for name in names:
            with open(os.path.join(folder, name), "rb") as file:
                images.append(prepare_image(file.read()))
        return images


def transcribe(image, reader):
    """
    Text einer Seite (JPEG-Bytes). Fehler des Anbieters werden zu
    ProviderError, ohne den Schlüssel in der Meldung.
    """
    import openai

    client = openai.OpenAI(
        api_key=reader.api_key or "none", base_url=reader.base_url or None,
        timeout=reader.timeout, max_retries=reader.max_retries,
    )
    picture = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}}
    # Ein reines OCR-Modell bekommt nur das Bild, jedes andere zusätzlich die Anweisung
    content = [picture] if reader.kind == "ocr" else [{"type": "text", "text": PROMPT}, picture]
    try:
        response = client.chat.completions.create(
            model=reader.model, messages=[{"role": "user", "content": content}],
            max_tokens=MAX_TOKENS, temperature=0,
        )
    except openai.APIError as error:
        raise ProviderError(_error_text(error, reader.api_key)) from error
    return (response.choices[0].message.content or "").strip()


def read_pages(images, reader):
    """
    Text aller Seiten (je eine Anfrage). Reicht die Zeit nicht für alle
    Seiten, steht ein Hinweis am Ende; wurde keine Seite gelesen, gibt es
    einen Fehler (der Aufrufer nimmt dann die lokale Texterkennung).
    """
    start = time.monotonic()
    texts = []
    for number, image in enumerate(images, start=1):
        if texts and time.monotonic() - start > TIME_BUDGET:
            texts.append(f"[Only the first {number - 1} of {len(images)} pages were read in time.]")
            break
        text = transcribe(image, reader)
        if text:
            texts.append(text if len(images) == 1 else f"--- Page {number} ---\n{text}")
    return "\n\n".join(texts).strip()


def read_pdf(data, reader):
    return read_pages(pdf_page_images(data), reader)


def read_image(path, reader):
    return read_pages([prepare_image(path)], reader)
