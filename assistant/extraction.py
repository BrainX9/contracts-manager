"""
Text aus Dateien im Chat lesen (Version 0.8b), ausschließlich lokal.

- PDF: Textebene mit pypdf; hat das PDF (fast) keinen Text, z. B. ein
  Scan, werden die Seiten mit poppler (pdftoppm) in Bilder umgewandelt und
  mit Tesseract erkannt (Deutsch und Englisch).
- Bilder (JPG, PNG, WebP): Tesseract.
- TXT: als Text (UTF-8, sonst Latin-1).
- EML: Betreff, Absender, Datum, Text der Nachricht und Text angehängter
  PDF-Dateien.

Erst wenn lokal kein Text erkannt wird, kann der Assistent die Datei
direkt an das Modell geben (assistant.agent, nur Claude).
"""

import email
import logging
import os
import subprocess
import tempfile
from email import policy
from io import BytesIO

from django.utils.html import strip_tags
from django.utils.translation import gettext_lazy as _

from contracts.documents import MAX_UPLOAD_SIZE, extension

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = ("pdf", "jpg", "jpeg", "png", "webp", "txt", "eml")
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
# Höchstzahl der Seiten für die Texterkennung (Dauer im Worker)
MAX_OCR_PAGES = 20
# Weniger Zeichen je Seite gilt als "kein Text" (Scan)
MIN_CHARS_PER_PAGE = 40
# Gespeicherter Text höchstens (Zeichen)
MAX_TEXT_LENGTH = 200_000
OCR_LANGUAGES = "deu+eng"
OCR_TIMEOUT = 120


def validate_upload(upload):
    """
    Prüft Endung und Größe einer Datei im Chat. Gibt eine Fehlermeldung
    zurück oder None.
    """
    if extension(upload.name) not in ALLOWED_EXTENSIONS:
        return _("Only PDF, images (JPG, PNG, WebP), text files and emails (EML) can be uploaded.")
    if upload.size > MAX_UPLOAD_SIZE:
        return _("The file may be at most 20 MB.")
    return None


def extract(path, name):
    """
    Liest den Text einer Datei. Gibt (Text, Verfahren) zurück; das
    Verfahren entspricht Attachment.Method.
    """
    ext = extension(name)
    try:
        if ext == "pdf":
            with open(path, "rb") as file:
                text, method = pdf_text(file.read())
        elif ext in IMAGE_EXTENSIONS:
            text, method = ocr_image(path), "ocr"
        elif ext == "txt":
            with open(path, "rb") as file:
                text, method = decode(file.read()), "text"
        elif ext == "eml":
            with open(path, "rb") as file:
                text, method = email_text(file.read()), "email"
        else:
            text, method = "", "none"
    except Exception:  # Defekte Dateien dürfen den Worker nicht anhalten
        logger.exception("Text konnte nicht gelesen werden: %s", name)
        text, method = "", "none"
    text = text.strip()[:MAX_TEXT_LENGTH]
    return (text, method) if text else ("", "none")


def decode(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def pdf_text(data):
    """
    Text eines PDFs; bei Scans Texterkennung. Gibt (Text, Verfahren) zurück.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        logger.warning("PDF-Text nicht lesbar, versuche Texterkennung")
        pages = []
    text = "\n\n".join(page.strip() for page in pages).strip()
    if pages and len(text) >= MIN_CHARS_PER_PAGE * len(pages):
        return text, "pdf_text"
    ocr = ocr_pdf(data)
    if len(ocr) > len(text):
        return ocr, "ocr"
    return text, "pdf_text"


def ocr_pdf(data):
    """
    Seiten eines PDFs als Bilder (300 dpi) erkennen, höchstens MAX_OCR_PAGES.
    """
    with tempfile.TemporaryDirectory() as folder:
        source = os.path.join(folder, "input.pdf")
        with open(source, "wb") as file:
            file.write(data)
        subprocess.run(
            ["pdftoppm", "-r", "300", "-png", "-l", str(MAX_OCR_PAGES), source, os.path.join(folder, "page")],
            check=True, capture_output=True, timeout=OCR_TIMEOUT,
        )
        images = sorted(name for name in os.listdir(folder) if name.startswith("page") and name.endswith(".png"))
        return "\n\n".join(ocr_image(os.path.join(folder, name)) for name in images).strip()


def ocr_image(path):
    """
    Texterkennung eines Bildes. WebP und andere Formate werden zuerst mit
    Pillow in PNG umgewandelt (Drehung aus den EXIF-Daten übernommen).
    """
    from PIL import Image, ImageOps

    with tempfile.TemporaryDirectory() as folder:
        target = os.path.join(folder, "image.png")
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.convert("RGB").save(target, "PNG")
        result = subprocess.run(
            ["tesseract", target, "stdout", "-l", OCR_LANGUAGES],
            check=True, capture_output=True, timeout=OCR_TIMEOUT,
        )
        return decode(result.stdout).strip()


def email_text(data):
    """
    Kopfzeilen und Text einer E-Mail, dazu der Text angehängter PDFs.
    """
    message = email.message_from_bytes(data, policy=policy.default)
    parts = [
        f"Subject: {message.get('subject', '')}",
        f"From: {message.get('from', '')}",
        f"To: {message.get('to', '')}",
        f"Date: {message.get('date', '')}",
        "",
    ]
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        content = body.get_content()
        parts.append(strip_tags(content) if body.get_content_type() == "text/html" else content)
    for part in message.iter_attachments():
        filename = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or filename.lower().endswith(".pdf"):
            text, _method = pdf_text(part.get_payload(decode=True) or b"")
            if text:
                parts += ["", f"--- Attachment: {filename} ---", text]
        elif filename:
            parts += ["", f"--- Attachment (not read): {filename} ---"]
    return "\n".join(parts)
