"""
Reparatur für Installationen mit der ersten Fassung von Version 0.8a.

Die Migration 0001 wurde nach dem Merge von 0.8a noch einmal neu erzeugt
(Felder Conversation.system_prompt, Message.provider und
Message.provider_content). Wer die erste Fassung schon ausgeführt hatte,
hat diese Spalten nicht, obwohl Django 0001 als erledigt ansieht; der
Assistent bricht dann mit "column ... does not exist" ab.

Diese Migration legt die Spalten nur an, wo sie fehlen (PostgreSQL
ADD COLUMN IF NOT EXISTS). Bei allen anderen Installationen ändert sie
nichts. Der Django-Zustand bleibt unverändert, die Felder stehen schon
in 0001.
"""

from django.db import migrations

ADD_COLUMNS = """
ALTER TABLE assistant_conversation ADD COLUMN IF NOT EXISTS system_prompt text NOT NULL DEFAULT '';
ALTER TABLE assistant_message ADD COLUMN IF NOT EXISTS provider varchar(20) NOT NULL DEFAULT '';
ALTER TABLE assistant_message ADD COLUMN IF NOT EXISTS provider_content jsonb NOT NULL DEFAULT '[]'::jsonb;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("assistant", "0002_attachments_proposals"),
    ]

    operations = [
        migrations.RunSQL(ADD_COLUMNS, reverse_sql=migrations.RunSQL.noop),
    ]
