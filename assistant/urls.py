from django.urls import path

from . import views

urlpatterns = [
    path("", views.AssistantHomeView.as_view(), name="assistant_home"),
    path("<int:pk>/", views.ConversationView.as_view(), name="assistant_conversation"),
    path("<int:pk>/messages/", views.ConversationMessagesView.as_view(), name="assistant_messages"),
    path("<int:pk>/delete/", views.ConversationDeleteView.as_view(), name="assistant_delete"),
    path("proposals/<int:pk>/<str:decision>/", views.ProposalDecisionView.as_view(), name="assistant_proposal"),
]
