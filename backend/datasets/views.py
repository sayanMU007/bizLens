from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import duck, storage
from .ingest import IngestError, ingest_upload
from .models import Dataset
from .serializers import DatasetDetailSerializer, DatasetSerializer, UploadSerializer

PREVIEW_MAX_LIMIT = 100


def error_response(code: str, message: str, details=None, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({"error": {"code": code, "message": message, "details": details or {}}},
                    status=http_status)


class DatasetListCreateView(GenericAPIView):
    """GET: list datasets. POST (multipart): upload a CSV/Excel file."""

    parser_classes = [MultiPartParser]
    serializer_class = UploadSerializer
    queryset = Dataset.objects.all()  # DRF's browsable API renderer requires this

    def get(self, request):
        return Response(DatasetSerializer(self.get_queryset(), many=True).data)

    def post(self, request):
        serializer = UploadSerializer(data=request.data)
        if not serializer.is_valid():
            message = "Invalid upload request."
            if "file" in serializer.errors:
                message = ("No usable file was received. Send it as a multipart form field named "
                           "'file' (in the browser page, use the HTML form and choose a file first).")
            return error_response("invalid_request", message, serializer.errors)
        try:
            dataset = ingest_upload(
                uploaded=serializer.validated_data["file"],
                name=serializer.validated_data.get("name") or None,
                dayfirst=serializer.validated_data["dayfirst"],
            )
        except IngestError as exc:
            return error_response(exc.code, exc.message, exc.details)
        return Response(DatasetDetailSerializer(dataset).data, status=status.HTTP_201_CREATED)


class DatasetDetailView(APIView):
    def get(self, request, pk):
        dataset = get_object_or_404(Dataset.objects.prefetch_related("columns"), pk=pk)
        return Response(DatasetDetailSerializer(dataset).data)

    def delete(self, request, pk):
        dataset = get_object_or_404(Dataset, pk=pk)
        dataset_id = dataset.id
        dataset.delete()
        storage.delete_dataset_files(dataset_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DatasetPreviewView(APIView):
    """Rows from the cleaned dataset, straight from DuckDB. Includes `_source_row`."""

    def get(self, request, pk):
        dataset = get_object_or_404(Dataset, pk=pk)
        try:
            limit = int(request.query_params.get("limit", 20))
            offset = int(request.query_params.get("offset", 0))
        except ValueError:
            return error_response("invalid_request", "limit and offset must be integers.")
        if not (1 <= limit <= PREVIEW_MAX_LIMIT) or offset < 0:
            return error_response(
                "invalid_request", f"limit must be 1-{PREVIEW_MAX_LIMIT} and offset >= 0.")
        try:
            result = duck.run_query(
                dataset,
                f"SELECT * FROM {duck.TABLE} ORDER BY _source_row LIMIT ? OFFSET ?",
                [limit, offset],
                max_rows=limit,
            )
        except FileNotFoundError:
            return error_response("data_missing", "The dataset's data file is missing.",
                                  http_status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({
            "columns": result.columns,
            "rows": [dict(zip(result.columns, row)) for row in result.rows],
            "total": dataset.row_count,
            "limit": limit,
            "offset": offset,
        })
