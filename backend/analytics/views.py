import json

from django.shortcuts import get_object_or_404
from pydantic import ValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from datasets import duck
from datasets.models import Dataset
from datasets.views import error_response

from .analyses import REGISTRY, run_analysis
from .errors import AnalysisError
from .metrics import METRICS


class CatalogView(APIView):
    """What can be asked: analyses (with parameter schemas) and metrics."""

    def get(self, request):
        return Response({
            "analyses": [{"name": a.name, "description": a.description,
                          "params_schema": a.params_model.model_json_schema()}
                         for a in REGISTRY.values()],
            "metrics": [{"name": m.name, "label": m.label, "unit": m.unit,
                         "additive": m.additive, "description": m.description}
                        for m in METRICS.values()],
        })


class AnalyzeView(APIView):
    """POST {"analysis": "<name>", "params": {...}} -> deterministic, fully traced result."""

    def post(self, request, pk):
        dataset = get_object_or_404(Dataset.objects.prefetch_related("columns"), pk=pk)
        body = request.data
        if not isinstance(body, dict):
            return error_response("invalid_request", "Send a JSON object.")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            return error_response("invalid_request", "'params' must be a JSON object.")
        try:
            result = run_analysis(dataset, body.get("analysis") or "", params)
        except AnalysisError as exc:
            return error_response(exc.code, exc.message, exc.details)
        except ValidationError as exc:
            return error_response("invalid_params", "The parameters are not valid.",
                                  {"errors": json.loads(exc.json(include_url=False,
                                                                 include_context=False))})
        except duck.QueryTimeout as exc:
            return error_response("query_timeout", str(exc),
                                  http_status=status.HTTP_504_GATEWAY_TIMEOUT)
        except FileNotFoundError:
            return error_response("data_missing", "The dataset's data file is missing.",
                                  http_status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(result.model_dump(mode="json"))
