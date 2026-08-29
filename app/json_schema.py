import json

import jsonschema
from jsonschema import validate as jsonschema_validate

SYSTEM_PROMPT = (
    "You must respond with a single valid JSON object that conforms exactly to "
    "this JSON schema, with no markdown fences, no commentary, and no keys outside "
    "the schema.\n\nSCHEMA:\n"
)


class JSONSchemaConflict(Exception):
    pass


def build_prompt(messages: list[dict], schema: dict) -> list[dict]:
    """Appends schema-enforcement instructions to the system prompt."""
    preamble = SYSTEM_PROMPT + json.dumps(schema, ensure_ascii=False)
    has_system = any(msg.get("role") == "system" for msg in messages)
    if has_system:
        enriched: list[dict] = []
        for message in messages:
            if message.get("role") == "system":
                enriched.append(
                    {"role": "system", "content": f"{message.get('content', '')}\n\n{preamble}"}
                )
            else:
                enriched.append(message)
        return enriched
    return [{"role": "system", "content": preamble}, *messages]


def validate_output(text: str, schema: dict) -> dict:
    """Parse an LLM output as JSON and validate it against the schema.

    Returns the parsed object. Raises SchemaValidationError (with readable
    details) when the output is not valid JSON or fails validation.
    """
    parsed = _parse_json(text)
    try:
        jsonschema_validate(parsed, schema)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(
            message=f"output failed JSON schema validation: {exc.message}",
            path=[str(p) for p in exc.absolute_path] or None,
            valid=False,
        )
    return parsed


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # strip fenced code blocks the model may have wrapped output in
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(
            message=f"output was not valid JSON: {exc.msg}",
            path=None,
            valid=False,
        )
    if not isinstance(parsed, dict):
        raise SchemaValidationError(
            message="output must be a JSON object",
            path=None,
            valid=False,
        )
    return parsed


class SchemaValidationError(ValueError):
    def __init__(self, message: str, path: list[str] | None, valid: bool) -> None:
        self.message = message
        self.path = path
        self.valid = valid
        super().__init__(message)