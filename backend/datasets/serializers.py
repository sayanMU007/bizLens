from rest_framework import serializers

from .models import Dataset, DatasetColumn


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="A .csv or .xlsx file with the sales columns.")
    name = serializers.CharField(required=False, max_length=200, allow_blank=True)
    dayfirst = serializers.BooleanField(
        required=False, default=False,
        help_text="Read ambiguous dates like 05/06/2025 as day/month (e.g. 5 June).",
    )


class DatasetColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetColumn
        fields = ["name", "position", "dtype", "role", "is_required"]


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = ["id", "name", "original_filename", "file_format", "file_size",
                  "sha256", "row_count", "created_at"]


class DatasetDetailSerializer(DatasetSerializer):
    columns = DatasetColumnSerializer(many=True, read_only=True)

    class Meta(DatasetSerializer.Meta):
        fields = DatasetSerializer.Meta.fields + ["columns", "profile"]
