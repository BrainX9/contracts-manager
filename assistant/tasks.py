"""
Aufgaben für den Worker (django-q2): Antworten des KI-Assistenten laufen
im Hintergrund, damit lange Anfragen den Webserver nicht blockieren.
"""

from django_q.tasks import async_task

from .models import Conversation


def answer_conversation(conversation_id):
    from .agent import answer

    conversation = Conversation.objects.select_related("user").filter(pk=conversation_id).first()
    if conversation is not None and conversation.is_running:
        answer(conversation)


def queue_answer(conversation):
    async_task("assistant.tasks.answer_conversation", conversation.pk, group="assistant")
