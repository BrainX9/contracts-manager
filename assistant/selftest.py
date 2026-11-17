"""
Test der KI-Einstellungen (Version 0.3.0), aufgerufen unter Verwaltung ›
KI-Assistent mit den eingegebenen, noch nicht gespeicherten Werten.

Fünf Schritte, jeder als eigene kurze Anfrage (``run_step``):

- ``connection``: Das Modell antwortet.
- ``tools``: Das Modell ruft ein Werkzeug auf (ohne geht der Assistent nicht).
- ``local``: Texterkennung im Container (PDF mit pypdf, Bild mit Tesseract).
- ``reader``: Die Dokument-KI liest ein Testbild (Version 0.6.0, nur wenn
  eingerichtet).
- ``file``: Datei-Upload an das Modell, auf demselben Weg wie im Chat
  (Werkzeug-Ergebnis mit Datei). Das gibt es nur bei Claude.

Die Testdateien entstehen bei jedem Lauf neu und enthalten eine zufällige
Zahl; gespeichert wird nichts.
"""

import dataclasses
import logging
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.utils.translation import gettext as _

from . import extraction, vision
from .config import OLLAMA
from .providers import ProviderError, RefusalError, get_provider

logger = logging.getLogger(__name__)

STEPS = ("connection", "tools", "local", "reader", "file")
OK, FAILED, SKIPPED = "ok", "failed", "skipped"

# Kürzer als die Zeitgrenze des Webservers (gunicorn, 60 Sekunden); der
# Schritt "file" braucht zwei Anfragen
TEST_TIMEOUT = 25

TEST_TOOL = {
    "name": "get_test_value",
    "description": "Returns the test value. Takes no arguments.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}
READ_TOOL = {
    "name": "read_attachment",
    "description": "Reads the uploaded document with the given id.",
    "parameters": {
        "type": "object",
        "properties": {"attachment_id": {"type": "integer"}},
        "required": ["attachment_id"],
    },
}
SYSTEM = "You are being used for a short connection test. Follow the instruction exactly and keep the answer short."


@dataclass
class StepResult:
    step: str
    status: str
    detail: str = ""

    @property
    def label(self):
        return step_label(self.step)


def step_label(step):
    return {
        "connection": _("Connection and answer"),
        "tools": _("Tool call"),
        "local": _("Local text recognition"),
        "reader": _("Document AI (scans and photos)"),
        "file": _("File upload to the model"),
    }[step]


def test_config(config, timeout=TEST_TIMEOUT):
    """Gleiche Konfiguration mit kurzer Zeitgrenze und ohne Wiederholungen."""
    reader = dataclasses.replace(config.reader, timeout=timeout, max_retries=0) if config.reader else None
    return dataclasses.replace(config, timeout=timeout, max_retries=0, reader=reader)


def new_code():
    return str(secrets.randbelow(900_000) + 100_000)


def pdf_bytes(text):
    """Kleinstes PDF mit einer Zeile Text (Helvetica), von Hand aufgebaut."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(data))
        data += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        data += b"%010d 00000 n \n" % offset
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return data


def png_bytes(text):
    """Bild mit einer Zeile Text für die Texterkennung."""
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(image).text((40, 60), text, fill="black", font=ImageFont.load_default(size=64))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _short(text, length=120):
    text = " ".join((text or "").split())
    return text if len(text) <= length else text[:length] + "…"


def check_connection(config):
    reply = get_provider(config).complete(
        SYSTEM, [{"role": "user", "content": "Reply with the single word OK."}], [],
    )
    if not reply.text.strip():
        return StepResult("connection", FAILED, _("The model returned an empty answer."))
    return StepResult("connection", OK, _("The model “%(model)s” answered: %(answer)s") % {
        "model": config.model, "answer": _short(reply.text),
    })


def check_tools(config):
    reply = get_provider(config).complete(
        SYSTEM, [{"role": "user", "content": "Call the tool get_test_value now."}], [TEST_TOOL],
    )
    if any(call["name"] == TEST_TOOL["name"] for call in reply.tool_calls):
        return StepResult("tools", OK, _("The model called the test tool."))
    return StepResult("tools", FAILED, _(
        "The model answered without calling the tool. The assistant needs a model that supports "
        "tools (function calling)."
    ))


def check_local(config=None):
    code = new_code()
    # Lang genug, damit das PDF nicht als Scan gilt (extraction.MIN_CHARS_PER_PAGE)
    text = f"Contracts Manager test document. Test code {code}"
    parts, failed = [], False
    with tempfile.TemporaryDirectory() as directory:
        for name, data, label in (
            ("test.pdf", pdf_bytes(text), _("PDF")),
            ("test.png", png_bytes(f"Test code {code}"), _("image (OCR)")),
        ):
            path = Path(directory) / name
            path.write_bytes(data)
            try:
                found, _method = extraction.extract(str(path), name)
            except Exception:  # fehlende Programme im Container nicht als 500 zeigen
                logger.exception("KI-Test: Texterkennung fehlgeschlagen (%s)", name)
                found = ""
            ok = code in (found or "").replace(" ", "")
            failed = failed or not ok
            parts.append(_("%(label)s: recognised") % {"label": label} if ok
                         else _("%(label)s: not recognised") % {"label": label})
    return StepResult("local", FAILED if failed else OK, ", ".join(str(part) for part in parts))


def check_reader(config):
    """
    Die Dokument-KI liest ein erzeugtes Bild mit einer Zahl (Version 0.6.0).
    Ohne eingerichtete Dokument-KI wird der Schritt übersprungen.
    """
    reader = config.reader
    if reader is None:
        return StepResult("reader", SKIPPED, _(
            "No document AI is set up. Scans and photos are read with the local text recognition."
        ))
    code = new_code()
    found = vision.read_pages([vision.prepare_image(png_bytes(f"Test code {code}"))], reader)
    if code in found.replace(" ", ""):
        return StepResult("reader", OK, _("The model “%(model)s” read the test image.") % {"model": reader.model})
    return StepResult("reader", FAILED, _(
        "The model did not read the test image correctly. Answer: %(answer)s"
    ) % {"answer": _short(found) or "–"})


def check_file(config):
    if config.provider != "anthropic":
        return StepResult("file", SKIPPED, _(
            "Files are only sent to the model with Claude (provider Anthropic). With this provider "
            "the assistant works with the locally recognised text."
        ))
    code = new_code()
    provider = get_provider(config)
    question = ("Read the attachment with read_attachment (attachment_id 1) and reply with the number "
                "that follows the words “Test code” in it. Reply with the number only.\n[Attachment #1: test.pdf]")
    messages = [{"role": "user", "content": question}]
    first = provider.complete(SYSTEM, messages, [READ_TOOL])
    calls = [call for call in first.tool_calls if call["name"] == READ_TOOL["name"]]
    if not calls:
        return StepResult("file", FAILED, _("The model did not ask for the file."))
    messages.append({"role": "assistant", "content": first.text, "tool_calls": first.tool_calls,
                     "provider": first.provider, "raw": first.raw})
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "test.pdf"
        path.write_bytes(pdf_bytes(f"Test code {code}"))
        for index, call in enumerate(first.tool_calls):
            entry = {"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                     "content": '{"text": "", "note": "No text was recognised locally; the file is attached."}'}
            if index == 0:
                entry["file"] = {"path": str(path), "media_type": "application/pdf"}
            messages.append(entry)
        second = provider.complete(SYSTEM, messages, [READ_TOOL])
    if code in second.text.replace(" ", ""):
        return StepResult("file", OK, _("The model read the uploaded PDF file."))
    return StepResult("file", FAILED, _("The model did not read the file correctly. Answer: %(answer)s") % {
        "answer": _short(second.text) or "–",
    })


def ollama_hint(config):
    """Mögliche Ursachen bei einem Fehler mit Ollama (Version 0.5.0), sonst leer."""
    if config.provider != OLLAMA:
        return ""
    return " " + _(
        "Ollama: check that it is running, that it listens on the network (OLLAMA_HOST=0.0.0.0), that "
        "the container can reach %(url)s and that the model “%(model)s” has been pulled."
    ) % {"url": config.base_url or "–", "model": config.model}


CHECKS = {
    "connection": check_connection, "tools": check_tools, "local": check_local,
    "reader": check_reader, "file": check_file,
}


def run_step(step, config, timeout=TEST_TIMEOUT):
    """
    Führt einen Schritt aus; Fehler des Anbieters werden zum Ergebnis.
    """
    try:
        return CHECKS[step](test_config(config, timeout))
    except RefusalError:
        return StepResult(step, FAILED, _("The model refused the test request."))
    except ProviderError as error:
        return StepResult(step, FAILED, f"{error}{ollama_hint(config)}")
    except Exception as error:  # z. B. ungültige Adresse
        logger.exception("KI-Test fehlgeschlagen (%s)", step)
        return StepResult(step, FAILED, f"{type(error).__name__}{ollama_hint(config)}")
