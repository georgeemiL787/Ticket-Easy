import json
import time
from ..config import AppError


def json_schema_format(schema):
    return {"type": "json_schema", "json_schema": {"name": "team_c", "strict": True, "schema": schema}}


def chat_completion(provider, body, api_key, step=None):
    """POST an OpenAI-compatible chat completion and return (answer text, response JSON); refusals and unfinished answers are invalid output."""
    try:
        value = provider.post(provider.base_url + "/chat/completions", body, {"Authorization": "Bearer " + api_key}, lambda response: json.loads(response.read()))
        if "error" in value:
            raise AppError("provider_request_failure", f"{provider.name} returned an error response", 502)
        choice = value["choices"][0]
        if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
            raise ValueError("Model refused or did not finish normally")
        return choice["message"]["content"], value
    finally:
        if step is not None:
            step["finished"] = time.time()
