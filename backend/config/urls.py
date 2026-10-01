from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", core_views.demo, name="demo"),
    path("demo/", core_views.demo),
    path("demo/sample.csv", core_views.demo_sample_csv),
    path("api/", include("core.urls")),
    path("api/", include("datasets.urls")),
    path("api/", include("analytics.urls")),
    path("api/", include("ask.urls")),
]
