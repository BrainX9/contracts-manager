import posixpath
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import connection, models, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.translation import gettext_lazy as _
from guardian.models import GroupObjectPermissionBase, UserObjectPermissionBase

from . import documents, logos, paperless

# Kennung für die PostgreSQL-Sperre bei der Vergabe von Contract.reference
REFERENCE_LOCK_ID = 2001

# Rundung auf Cent für Summen
CENT = Decimal('0.01')


class Country(models.TextChoices):
    """
    Länderauswahl für Vertragspartner (ISO-3166-Code, übersetzbarer Name)
    """
    DE = 'DE', _('Germany')
    AT = 'AT', _('Austria')
    CH = 'CH', _('Switzerland')
    NL = 'NL', _('Netherlands')
    BE = 'BE', _('Belgium')
    LU = 'LU', _('Luxembourg')
    FR = 'FR', _('France')
    IT = 'IT', _('Italy')
    ES = 'ES', _('Spain')
    PL = 'PL', _('Poland')
    CZ = 'CZ', _('Czechia')
    DK = 'DK', _('Denmark')
    GB = 'GB', _('United Kingdom')
    IE = 'IE', _('Ireland')
    SE = 'SE', _('Sweden')
    US = 'US', _('United States')
    OTHER = 'XX', _('Other')


class OwnedModel(models.Model):
    """
    Basis für Objekte mit Berechtigungen (Version 0.3a beta).

    - Eigentümer: Feld "owner", genau einer pro Objekt, hat alle Rechte
      (siehe contracts.backends.OwnerBackend). PROTECT verhindert, dass ein
      Benutzer gelöscht wird, solange ihm Objekte gehören.
    - Leser und Bearbeiter: Rechte pro Objekt über django-guardian
      (siehe contracts.permissions).
    """
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_%(class)s_set",
        verbose_name=_("owner"),
    )

    class Meta:
        abstract = True


class Provider(OwnedModel):
    """
    Stammdaten: Vertragspartner
    """
    name = models.CharField(_("name"), max_length=200)
    street = models.CharField(_("street"), max_length=255, blank=True)
    postal_code = models.CharField(_("postal code"), max_length=20, blank=True)
    city = models.CharField(_("city"), max_length=100, blank=True)
    country = models.CharField(_("country"), max_length=2, choices=Country.choices, default=Country.DE)
    email = models.EmailField(_("email"), blank=True)
    customer_number = models.CharField(_("customer number"), max_length=100, blank=True)
    contact_person = models.CharField(_("contact person"), max_length=200, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    # Version 0.2 beta: Verknüpfung mit Paperless-ngx
    paperless_correspondent_id = models.PositiveIntegerField(
        _("correspondent ID (Paperless)"),
        null=True, blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("ID of the correspondent in Paperless-ngx"),
    )
    # Version 0.4 beta: Logo (siehe contracts.logos)
    logo = models.ImageField(
        _("logo"),
        upload_to=logos.logo_upload_to,
        blank=True,
        validators=[logos.validate_logo],
        help_text=_("PNG, JPEG or WebP, at most 2 MB. It is reduced to 256 pixels."),
    )

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Neu hochgeladene Logos verkleinern und ohne Metadaten speichern
        if self.logo and not self.logo._committed:
            self.logo.save("logo.png", logos.process_image(self.logo), save=False)
        super().save(*args, **kwargs)

    @property
    def paperless_correspondent_url(self):
        """
        Link zu den Dokumenten dieses Korrespondenten in Paperless (oder None).
        """
        return paperless.correspondent_url(self.paperless_correspondent_id)

    class Meta:
        verbose_name = _("contract partner")
        verbose_name_plural = _("contract partners")
        constraints = [
            # Eindeutig pro Eigentümer, damit andere Benutzer nicht über
            # Fehlermeldungen von fremden Partnern erfahren
            models.UniqueConstraint(fields=["owner", "name"], name="unique_provider_owner_name"),
        ]


class Category(OwnedModel):
    """
    Stammdaten: Kategorie
    """
    name = models.CharField(_("name"), max_length=200)
    parent = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True, related_name='children',
        verbose_name=_("main category"),
    )

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} / {self.name}"
        return self.name

    def clean(self):
        # Prüfe, ob die Kategorie zwei Ebenen hat (überprüfung auf mehr als zwei Ebenen)
        if self.parent and self.parent.parent:
            raise ValidationError(_("Only two levels are allowed: main categories and subcategories."))
        # Eine Kategorie mit Unterkategorien kann nicht selbst Unterkategorie
        # werden, sonst entstünden drei Ebenen (Version 0.3d)
        if self.parent_id is not None and self.pk is not None:
            if self.parent_id == self.pk:
                raise ValidationError(_("A category cannot be its own main category."))
            if self.children.exists():
                raise ValidationError(
                    _("A category with subcategories cannot become a subcategory itself.")
                )

    def save(self, *args, **kwargs):
        self.full_clean()  # Validierung aufrufen
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _("category")
        verbose_name_plural = _("categories")
        constraints = [
            # Eindeutig pro Eigentümer und Hauptkategorie
            models.UniqueConstraint(
                fields=["owner", "parent", "name"], nulls_distinct=False,
                name="unique_category_owner_parent_name",
            )
        ]


def category_tree_order(queryset):
    """
    Sortiert Kategorien wie einen Baum: jede Hauptkategorie, direkt danach
    ihre Unterkategorien (alphabetisch).
    """
    from django.db.models import F
    from django.db.models.functions import Coalesce, Lower

    return queryset.select_related("parent").order_by(
        Lower(Coalesce("parent__name", "name")),
        F("parent").asc(nulls_first=True),
        Lower("name"),
    )


class Consumer(OwnedModel):
    """
    Stammdaten: Verbraucher (Person, Fahrzeug, Haushalt)
    """
    name = models.CharField(_("name"), max_length=200)
    # Version 0.2 beta. Text statt Zahl, damit auch Kennzeichen oder
    # Nummern mit führender Null passen.
    number = models.CharField(_("number"), max_length=100, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("consumer")
        verbose_name_plural = _("consumers")
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_consumer_owner_name"),
        ]


class PaymentMethod(OwnedModel):
    """
    Stammdaten: Zahlungsmethode (Version 0.2.0), z. B. PayPal oder
    Lastschrift. Nicht zu verwechseln mit der Zahlweise
    (CostEntry.payment_interval).
    """
    name = models.CharField(_("name"), max_length=200)
    # Gleiche Regeln wie Provider.logo (siehe contracts.logos)
    icon = models.ImageField(
        _("icon"),
        upload_to=logos.payment_icon_upload_to,
        blank=True,
        validators=[logos.validate_image],
        help_text=_("PNG, JPEG or WebP, at most 2 MB. It is reduced to 256 pixels."),
    )

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Neu hochgeladene Icons verkleinern und ohne Metadaten speichern
        if self.icon and not self.icon._committed:
            self.icon.save("icon.png", logos.process_image(self.icon), save=False)
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _("payment method")
        verbose_name_plural = _("payment methods")
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_paymentmethod_owner_name"),
        ]


class Contract(OwnedModel):
    """
    Vertrag
    """
    name = models.CharField(_("name"), max_length=200)
    # PROTECT: Stammdaten mit Verträgen können nicht gelöscht werden
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, verbose_name=_("contract partner"))
    # Optional seit 0.9 beta (z. B. alte Verträge ohne bekanntes Datum)
    start_date = models.DateField(_("start date"), null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, verbose_name=_("category"))
    # Optional seit 0.9 beta
    consumer = models.ForeignKey(
        Consumer, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_("consumer"),
    )
    # Version 0.2.0, optional
    payment_method = models.ForeignKey(
        PaymentMethod, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_("payment method"),
    )

    # Neue Felder für Phase 1b
    reference = models.CharField(
        _("reference"), max_length=200, unique=True, editable=False, null=True, blank=True,
    )
    contract_number = models.CharField(_("contract number"), max_length=200, blank=True)
    short_description = models.CharField(_("short description"), max_length=255, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    term_value = models.PositiveIntegerField(_("term"), null=True, blank=True)
    term_unit = models.CharField(
        _("term unit"),
        max_length=20,
        choices=[
            ('days', _('days')),
            ('weeks', _('weeks')),
            ('months', _('months')),
            ('quarters', _('quarters')),
            ('years', _('years')),
        ],
        null=True, blank=True
    )

    def __str__(self):
        return self.name

    @property
    def current_cost_entry(self):
        """
        Gibt den aktuell gültigen Kosteneintrag zurück (oder None).
        Das ist der Eintrag mit dem jüngsten 'valid_from', das nicht in der
        Zukunft liegt. Bei gleichem Datum gilt der zuletzt angelegte Eintrag.

        Wurden die Kosten mit prefetch_related("costs") geladen, entsteht
        keine weitere Abfrage (wichtig für Listen mit vielen Verträgen).
        """
        today = timezone.localdate()
        if "costs" in getattr(self, "_prefetched_objects_cache", {}):
            valid = [entry for entry in self.costs.all() if entry.valid_from <= today]
            return max(valid, key=lambda entry: (entry.valid_from, entry.id), default=None)
        return (
            self.costs.filter(valid_from__lte=today)
            .order_by('-valid_from', '-id')
            .first()
        )

    @property
    def current_cost(self):
        """
        Gibt den aktuellen Betrag des Vertrags zurück (in seiner Zahlweise).
        """
        entry = self.current_cost_entry
        return entry.amount if entry else None

    @property
    def current_monthly_cost(self):
        """
        Gibt den aktuellen Betrag auf einen Monat umgerechnet zurück (ungerundet).
        """
        entry = self.current_cost_entry
        return entry.monthly_amount() if entry else None

    def clean(self):
        # Validierung: term_value und term_unit müssen zusammen gesetzt oder leer sein
        if (self.term_value is not None and self.term_unit is None) or \
           (self.term_value is None and self.term_unit is not None):
            raise ValidationError(_("Term and term unit must either both be set or both be empty."))

    def save(self, *args, **kwargs):
        # Vergabe der Nummer und Speichern in einer Transaktion, damit die
        # Sperre erst nach dem Einfügen des Datensatzes freigegeben wird.
        with transaction.atomic():
            if not self.reference:
                self.reference = self._next_reference()
            self.full_clean()  # Validierung aufrufen
            super().save(*args, **kwargs)

    @staticmethod
    def _next_reference():
        """
        Ermittelt die nächste Vertragsnummer im Format YYYY + 4-stelliger Zähler.
        Muss innerhalb einer Transaktion aufgerufen werden.
        """
        # Lokales Datum (Europe/Berlin), damit der Jahreswechsel um Mitternacht gilt
        current_year = timezone.localdate().year

        # Sperre pro Jahr bis zum Ende der Transaktion. Gleichzeitige
        # Speichervorgänge warten hier, auch beim ersten Vertrag eines
        # Jahres (select_for_update würde dann keine Zeile sperren).
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                [REFERENCE_LOCK_ID, current_year],
            )

        # Höchste bisher vergebene Nummer dieses Jahres
        latest_contract = Contract.objects.filter(
            reference__startswith=str(current_year)
        ).order_by('-reference').first()

        if latest_contract:
            new_counter = int(latest_contract.reference[-4:]) + 1
        else:
            # Neuer Zähler für das Jahr
            new_counter = 1

        return f"{current_year}{new_counter:04d}"

    class Meta:
        verbose_name = _("contract")
        verbose_name_plural = _("contracts")


class Document(models.Model):
    """
    Dokument zu einem Vertrag (Version 0.7 beta): hochgeladene Datei oder
    Verweis auf ein Dokument in Paperless-ngx. Rechte folgen dem Vertrag
    wie bei Kosteneinträgen (lesen mit Leserecht, anlegen/ändern/löschen
    mit Bearbeitungsrecht). Ablage und Prüfung: contracts.documents.
    """

    class Source(models.TextChoices):
        UPLOAD = "upload", _("Local")
        PAPERLESS = "paperless", _("Paperless")

    contract = models.ForeignKey(
        Contract, on_delete=models.CASCADE, related_name="documents", verbose_name=_("contract"),
    )
    name = models.CharField(_("name"), max_length=200)
    source = models.CharField(_("source"), max_length=20, choices=Source.choices, default=Source.UPLOAD)
    file = models.FileField(
        _("file"), upload_to=documents.document_upload_to, max_length=documents.MAX_PATH_LENGTH,
        blank=True, validators=[documents.validate_document_file],
    )
    paperless_document_id = models.PositiveIntegerField(
        _("document ID (Paperless)"), null=True, blank=True, validators=[MinValueValidator(1)],
        help_text=_("ID of the document in Paperless-ngx (shown in its address, e.g. /documents/123/details)."),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("document")
        verbose_name_plural = _("documents")
        ordering = ["-created_at", "-id"]
        constraints = [
            # Genau eine Quelle: Datei bei Upload, ID bei Paperless
            models.CheckConstraint(
                condition=(
                    models.Q(source="upload", paperless_document_id__isnull=True) & ~models.Q(file="")
                ) | models.Q(source="paperless", paperless_document_id__isnull=False, file=""),
                name="document_source_matches",
            ),
            models.UniqueConstraint(
                fields=["contract", "paperless_document_id"], name="unique_paperless_document_per_contract",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_upload(self):
        return self.source == self.Source.UPLOAD

    @property
    def filename(self):
        return posixpath.basename(self.file.name) if self.file else ""

    @property
    def file_size(self):
        """
        Größe der Datei in Bytes (oder None, z. B. wenn sie fehlt).
        """
        if not self.file:
            return None
        try:
            return self.file.size
        except OSError:
            return None

    @property
    def opens_inline(self):
        """
        PDFs und Bilder öffnet der Browser direkt (neuer Tab).
        """
        return bool(self.file) and documents.is_inline(self.file.name)

    @property
    def paperless_url(self):
        """
        Link zum Dokument in Paperless (oder None, auch ohne PAPERLESS_URL).
        """
        if self.is_upload:
            return None
        return paperless.document_url(self.paperless_document_id)

    def clean(self):
        if self.is_upload:
            self.paperless_document_id = None
            if not self.file:
                raise ValidationError({"file": _("Please choose a file.")})
        else:
            if self.file:
                raise ValidationError({"file": _("A Paperless document has no file.")})
            if self.paperless_document_id is None:
                raise ValidationError({"paperless_document_id": _("Please enter the document ID from Paperless.")})


class CostEntry(models.Model):
    """
    Kosten mit Startdatum
    """
    contract = models.ForeignKey(
        Contract, on_delete=models.CASCADE, related_name='costs', verbose_name=_("contract"),
    )
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    valid_from = models.DateField(_("valid from"))
    payment_interval = models.CharField(
        _("payment interval"),
        max_length=20,
        choices=[
            ('weekly', _('weekly')),
            ('monthly', _('monthly')),
            ('quarterly', _('quarterly')),
            ('yearly', _('yearly')),
        ],
        default='monthly'
    )
    # Version 0.2 beta
    notes = models.TextField(_("notes"), blank=True)
    # Version 0.7 beta: Verweis auf ein Dokument desselben Vertrags
    document = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="cost_entries",
        verbose_name=_("document"), help_text=_("e.g. invoice or notice of a price change"),
    )

    def __str__(self):
        return _("%(contract)s – %(amount)s from %(date)s") % {
            "contract": self.contract.name, "amount": self.amount, "date": self.valid_from,
        }

    def clean(self):
        # Nur Dokumente desselben Vertrags
        if self.document_id and self.contract_id and self.document.contract_id != self.contract_id:
            raise ValidationError({"document": _("The document belongs to another contract.")})

    def monthly_amount(self):
        """
        Berechnet den Betrag auf einen Monat umgerechnet
        """
        if self.payment_interval == 'weekly':
            # 52 Wochen pro Jahr / 12 Monate = ca. 4.3333 Wochen pro Monat  
            return self.amount * 52 / 12
        elif self.payment_interval == 'quarterly':
            # 1 Quartal = 3 Monate
            return self.amount / 3
        elif self.payment_interval == 'yearly':
            # 1 Jahr = 12 Monate
            return self.amount / 12
        else:  # monthly
            return self.amount

    class Meta:
        verbose_name = _("cost entry")
        verbose_name_plural = _("cost entries")
        ordering = ['valid_from']


# Rechte-Tabellen für django-guardian mit direktem Fremdschlüssel je Modell
# ("Direct foreign keys"). Vorteile gegenüber den generischen Tabellen:
# Freigaben werden mit dem Objekt gelöscht, Abfragen sind schneller.

class Reminder(models.Model):
    """
    Erinnerung per E-Mail zu einem Vertrag (Version 0.5 beta), höchstens
    eine pro Vertrag. Rechte folgen dem Vertrag wie bei Kosteneinträgen.

    Versand täglich ab ``remind_on - lead_days`` bis einschließlich
    ``remind_on`` (contracts.reminders). Danach schaltet sich die
    Erinnerung aus und gilt als erledigt (``completed_on``).
    """
    MAX_LEAD_DAYS = 60

    class Reason(models.TextChoices):
        CANCELLATION = "cancellation", _("Cancellation")
        REVIEW = "review", _("Contract review")
        OTHER = "other", _("Other")

    contract = models.OneToOneField(
        Contract, on_delete=models.CASCADE, related_name="reminder", verbose_name=_("contract"),
    )
    enabled = models.BooleanField(_("reminder active"), default=False)
    remind_on = models.DateField(_("reminder date"), null=True, blank=True)
    lead_days = models.PositiveSmallIntegerField(
        _("remind in advance"),
        default=7,
        choices=[(days, days) for days in range(MAX_LEAD_DAYS + 1)],
        validators=[MaxValueValidator(MAX_LEAD_DAYS)],
        help_text=_("Days before the reminder date from which an email is sent every day."),
    )
    reason = models.CharField(_("reason"), max_length=20, choices=Reason.choices, default=Reason.CANCELLATION)
    note = models.TextField(_("note"), blank=True, help_text=_("Included in the email."))
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="contract_reminders", verbose_name=_("recipients"),
    )
    # Intern: Schutz vor doppeltem Versand und Zeitpunkt der Erledigung
    last_sent_on = models.DateField(_("last sent on"), null=True, blank=True, editable=False)
    completed_on = models.DateField(_("completed on"), null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("reminder")
        verbose_name_plural = _("reminders")

    def __str__(self):
        return f"{self.contract} – {self.get_reason_display()}"

    @property
    def send_from(self):
        """
        Erster Versandtag (oder None ohne Datum).
        """
        if self.remind_on is None:
            return None
        return self.remind_on - timedelta(days=self.lead_days)

    def is_due(self, today=None):
        """
        Ob heute eine E-Mail fällig ist (aktiv, im Zeitraum, heute noch nicht versendet).
        """
        today = today or timezone.localdate()
        return (
            self.enabled
            and self.remind_on is not None
            and self.send_from <= today <= self.remind_on
            and self.last_sent_on != today
        )

    @property
    def status(self):
        """
        "off", "done", "scheduled" (Versand beginnt später) oder "sending".
        """
        if not self.enabled:
            return "done" if self.completed_on else "off"
        if timezone.localdate() < self.send_from:
            return "scheduled"
        return "sending"

    @property
    def days_left(self):
        if self.remind_on is None:
            return None
        return (self.remind_on - timezone.localdate()).days

    def clean(self):
        if self.enabled and self.remind_on is None:
            raise ValidationError({"remind_on": _("Please enter a reminder date.")})
        if self.enabled and self.remind_on < timezone.localdate():
            raise ValidationError({"remind_on": _("The reminder date must not be in the past.")})

    def save(self, *args, **kwargs):
        # Wieder eingeschaltet: nicht mehr erledigt
        if self.enabled:
            self.completed_on = None
        super().save(*args, **kwargs)


class ProviderUserObjectPermission(UserObjectPermissionBase):
    content_object = models.ForeignKey(Provider, on_delete=models.CASCADE)

    class Meta(UserObjectPermissionBase.Meta):
        verbose_name = _("contract partner share (user)")
        verbose_name_plural = _("contract partner shares (users)")


class ProviderGroupObjectPermission(GroupObjectPermissionBase):
    content_object = models.ForeignKey(Provider, on_delete=models.CASCADE)

    class Meta(GroupObjectPermissionBase.Meta):
        verbose_name = _("contract partner share (group)")
        verbose_name_plural = _("contract partner shares (groups)")


class CategoryUserObjectPermission(UserObjectPermissionBase):
    content_object = models.ForeignKey(Category, on_delete=models.CASCADE)

    class Meta(UserObjectPermissionBase.Meta):
        verbose_name = _("category share (user)")
        verbose_name_plural = _("category shares (users)")


class CategoryGroupObjectPermission(GroupObjectPermissionBase):
    content_object = models.ForeignKey(Category, on_delete=models.CASCADE)

    class Meta(GroupObjectPermissionBase.Meta):
        verbose_name = _("category share (group)")
        verbose_name_plural = _("category shares (groups)")


class ConsumerUserObjectPermission(UserObjectPermissionBase):
    content_object = models.ForeignKey(Consumer, on_delete=models.CASCADE)

    class Meta(UserObjectPermissionBase.Meta):
        verbose_name = _("consumer share (user)")
        verbose_name_plural = _("consumer shares (users)")


class ConsumerGroupObjectPermission(GroupObjectPermissionBase):
    content_object = models.ForeignKey(Consumer, on_delete=models.CASCADE)

    class Meta(GroupObjectPermissionBase.Meta):
        verbose_name = _("consumer share (group)")
        verbose_name_plural = _("consumer shares (groups)")


class PaymentMethodUserObjectPermission(UserObjectPermissionBase):
    content_object = models.ForeignKey(PaymentMethod, on_delete=models.CASCADE)

    class Meta(UserObjectPermissionBase.Meta):
        verbose_name = _("payment method share (user)")
        verbose_name_plural = _("payment method shares (users)")


class PaymentMethodGroupObjectPermission(GroupObjectPermissionBase):
    content_object = models.ForeignKey(PaymentMethod, on_delete=models.CASCADE)

    class Meta(GroupObjectPermissionBase.Meta):
        verbose_name = _("payment method share (group)")
        verbose_name_plural = _("payment method shares (groups)")


class ContractUserObjectPermission(UserObjectPermissionBase):
    content_object = models.ForeignKey(Contract, on_delete=models.CASCADE)

    class Meta(UserObjectPermissionBase.Meta):
        verbose_name = _("contract share (user)")
        verbose_name_plural = _("contract shares (users)")


class ContractGroupObjectPermission(GroupObjectPermissionBase):
    content_object = models.ForeignKey(Contract, on_delete=models.CASCADE)

    class Meta(GroupObjectPermissionBase.Meta):
        verbose_name = _("contract share (group)")
        verbose_name_plural = _("contract shares (groups)")


def total_current_costs(contracts):
    """
    Summiert die aktuellen Kosten der übergebenen Verträge.

    Grundlage ist der auf einen Monat umgerechnete Betrag jedes Vertrags.
    Verträge ohne aktuell gültigen Kosteneintrag zählen nicht mit.
    Gibt ein Dict mit 'monthly', 'yearly' (beide auf 2 Nachkommastellen
    gerundet) und 'count' (Anzahl der Verträge mit Kosten) zurück.
    """
    monthly_sum = Decimal('0')
    count = 0
    for contract in contracts:
        monthly = contract.current_monthly_cost
        if monthly is not None:
            monthly_sum += monthly
            count += 1

    # Erst am Ende runden, damit sich Rundungsfehler nicht aufsummieren
    return {
        'monthly': monthly_sum.quantize(CENT, rounding=ROUND_HALF_UP),
        'yearly': (monthly_sum * 12).quantize(CENT, rounding=ROUND_HALF_UP),
        'count': count,
    }
