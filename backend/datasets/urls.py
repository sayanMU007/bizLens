from django.urls import path

from . import views

urlpatterns = [
    path("datasets/", views.DatasetListCreateView.as_view(), name="dataset-list"),
    path("datasets/<uuid:pk>/", views.DatasetDetailView.as_view(), name="dataset-detail"),
    path("datasets/<uuid:pk>/preview/", views.DatasetPreviewView.as_view(), name="dataset-preview"),
]
