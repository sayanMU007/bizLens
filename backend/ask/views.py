from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from ai.provider import LLMConfigurationError, LLMResponseError, LLMTransientError
from datasets import duck
from datasets.models import Dataset
from datasets.views import error_response

from .pipeline import AskError, ask


class AskView(APIView):
    """POST {"question": "..."} -> answer + evidence (queries, measurements, tables) + trace."""

    throttle_classes = [AnonRateThrottle]  # per-IP; protects the LLM key on a public deploy

    def post(self, request, pk):
        dataset = get_object_or_404(Dataset.objects.prefetch_related("columns"), pk=pk)
        body = request.data
        if not isinstance(body, dict):
            return error_response("invalid_request", "Send a JSON object with a 'question'.")
        try:
            response = ask(dataset, body.get("question"))
        except AskError as exc:
            return error_response(exc.code, exc.message,
                                  {"trace": [t.model_dump() for t in exc.trace]},
                                  http_status=status.HTTP_400_BAD_REQUEST
                                  if exc.code == "invalid_question"
                                  else status.HTTP_422_UNPROCESSABLE_ENTITY)
        except LLMConfigurationError as exc:
            return error_response("llm_not_configured", str(exc),
                                  http_status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except LLMTransientError:
            return error_response("llm_unavailable",
                                  "The AI service is busy or unreachable. Please try again.",
                                  http_status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except LLMResponseError:
            return error_response("llm_bad_response",
                                  "The AI service returned an unusable response. Please try again.",
                                  http_status=status.HTTP_502_BAD_GATEWAY)
        except duck.QueryTimeout as exc:
            return error_response("query_timeout", str(exc),
                                  http_status=status.HTTP_504_GATEWAY_TIMEOUT)
        except FileNotFoundError:
            return error_response("data_missing", "The dataset's data file is missing.",
                                  http_status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(response.model_dump(mode="json"))
