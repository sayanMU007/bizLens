from django.conf import settings
from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Liveness + config check. Never calls the LLM (costs money) and never returns secrets."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    body = {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "llm": {
            "provider": settings.LLM_PROVIDER,
            "configured": bool(settings.GROQ_API_KEY),
            "model": settings.GROQ_MODEL,
        },
    }
    return Response(body, status=200 if db_ok else 503)
