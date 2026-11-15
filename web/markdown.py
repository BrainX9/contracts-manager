"""
Markdown in Notizen (Version 0.4.0): Vertrag, Vertragspartner, Verbraucher.

Umsetzung mit markdown-it-py. Regeln:
- HTML in der Notiz wird nicht ausgeführt, sondern als Text angezeigt.
- Links nur mit unbedenklichem Schema (markdown-it lehnt z. B.
  ``javascript:`` ab); sie öffnen in einem neuen Tab ohne Bezug zur App.
- Keine Bilder: Sie würden beim Öffnen einer Notiz fremde Server aufrufen.
- Ein einfacher Zeilenumbruch bleibt ein Zeilenumbruch, damit Notizen aus
  früheren Versionen (reiner Text) unverändert aussehen.
"""

from functools import lru_cache

from django.utils.safestring import mark_safe
from markdown_it import MarkdownIt


def render_link_open(renderer, tokens, idx, options, env):
    token = tokens[idx]
    token.attrSet("target", "_blank")
    token.attrSet("rel", "noopener noreferrer nofollow")
    return renderer.renderToken(tokens, idx, options, env)


@lru_cache(maxsize=1)
def parser():
    # "js-default": CommonMark mit Tabellen und Durchstreichen, ohne HTML
    md = MarkdownIt("js-default", {"html": False, "breaks": True, "linkify": False, "typographer": False})
    md.disable("image")
    md.add_render_rule("link_open", render_link_open)
    return md


def render(text):
    """Notiz als HTML; die Ausgabe ist sicher (kein HTML aus der Eingabe)."""
    if not text:
        return ""
    return mark_safe(parser().render(str(text)))
