from django.urls import path

from . import views

urlpatterns = [path("datasets/<uuid:pk>/ask/", views.AskView.as_view(), name="dataset-ask")]
