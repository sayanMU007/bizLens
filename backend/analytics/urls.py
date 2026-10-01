from django.urls import path

from . import views

urlpatterns = [
    path("analytics/catalog/", views.CatalogView.as_view(), name="analytics-catalog"),
    path("datasets/<uuid:pk>/analyze/", views.AnalyzeView.as_view(), name="dataset-analyze"),
]
