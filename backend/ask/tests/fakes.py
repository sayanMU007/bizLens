from ai.provider import LLMProvider, LLMUsage, StructuredResult
from ai.schemas import AnalysisPlan, GeneratedSQL


class ScriptedProvider(LLMProvider):
    """Returns pre-baked outputs in order; records the user prompts it was sent."""
    name = "fake"

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def generate_structured(self, *, system, user, schema, tier=None, temperature=None,
                            max_output_tokens=None):
        self.calls.append((schema, user))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        assert isinstance(out, schema), f"scripted {type(out)} but asked for {schema}"
        return StructuredResult(data=out, provider="fake", model="fake-1",
                                usage=LLMUsage(0, 0, 10), latency_ms=1)


def plan(**kw) -> AnalysisPlan:
    base = dict(route="analysis", reasoning="r", analysis=None, metric=None, period=None,
                comparison=None, dimension=None, dimensions=None, filters=[], n=None,
                order=None, direction=None, sql_goal=None, message=None)
    base.update(kw)
    return AnalysisPlan(**base)


def sql(text, explanation="e") -> GeneratedSQL:
    return GeneratedSQL(sql=text, explanation=explanation)
