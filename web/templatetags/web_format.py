from django import template
from django.utils.formats import number_format

register = template.Library()


@register.filter
def euro(amount):
    """
    Betrag mit zwei Nachkommastellen im Format der aktiven Sprache und
    Euro-Zeichen, z. B. "1.234,50 €". Leer bleibt "–".
    """
    if amount is None or amount == "":
        return "–"
    return f"{number_format(amount, decimal_pos=2, force_grouping=True)} €"
