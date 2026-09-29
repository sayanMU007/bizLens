from typing import Any, Literal

from django.test import SimpleTestCase
from pydantic import BaseModel, Field

from ai.strict_schema import StrictSchemaError, to_strict_json_schema


class Inner(BaseModel):
    label: str
    weight: float = Field(ge=0, le=1)


class Outer(BaseModel):
    title: str  # a *field* named "title" must survive
    kind: Literal["a", "b"]
    note: str | None = None
    items: list[Inner] = Field(max_length=5)


class OpenDict(BaseModel):
    payload: dict[str, Any]


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


class StrictSchemaTests(SimpleTestCase):
    def test_all_objects_closed_and_fully_required(self):
        schema = to_strict_json_schema(Outer)
        objs = list(_objects(schema))
        self.assertGreaterEqual(len(objs), 2)  # Outer + Inner ($defs)
        for obj in objs:
            self.assertIs(obj["additionalProperties"], False)
            self.assertEqual(set(obj["required"]), set(obj["properties"]))

    def test_optional_field_is_required_but_nullable(self):
        schema = to_strict_json_schema(Outer)
        self.assertIn("note", schema["required"])
        self.assertIn({"type": "null"}, schema["properties"]["note"]["anyOf"])

    def test_field_named_title_is_preserved(self):
        self.assertIn("title", to_strict_json_schema(Outer)["properties"])

    def test_unsupported_keywords_are_stripped(self):
        text = str(to_strict_json_schema(Outer))
        for key in ("'minimum'", "'maximum'", "'maxItems'", "'default'"):
            self.assertNotIn(key, text)

    def test_const_is_rewritten_to_enum(self):
        class OneValue(BaseModel):
            status: Literal["ok"]

        prop = to_strict_json_schema(OneValue)["properties"]["status"]
        self.assertEqual(prop["enum"], ["ok"])
        self.assertNotIn("const", prop)

    def test_open_ended_dict_is_rejected(self):
        with self.assertRaises(StrictSchemaError):
            to_strict_json_schema(OpenDict)
