import httpx
from ..config import AppError
from ..progress import check_cancelled


class Provider:
    """One configured model endpoint. Subclasses build requests and parse answers; the router handles fallback."""
    name = ""

    def __init__(self, settings, store, transport=None):
        self.settings, self.store, self.transport = settings, store, transport

    @property
    def model(self):
        return getattr(self.settings, self.name + "_model")

    @property
    def base_url(self):
        return getattr(self.settings, self.name + "_base_url").rstrip("/")

    @property
    def timeout(self):
        return getattr(self.settings, self.name + "_timeout")

    @classmethod
    def check_configuration(cls, settings):
        if not getattr(settings, cls.name + "_model") or not getattr(settings, cls.name + "_base_url"):
            raise AppError("model_configuration", f"Missing {cls.name} model or endpoint", 503)

    def is_service_failure(self, response):
        return response.status_code == 429 or response.status_code >= 500

    def post(self, url, body, headers, read):
        """POST and read successful responses; only classified service failures are eligible for fallback."""
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", url, json=body, headers=headers) as response:
                    check_cancelled()
                    if self.is_service_failure(response):
                        raise AppError("provider_service_failure", f"{self.name} returned HTTP {response.status_code}", 502)
                    if response.status_code >= 300:
                        raise AppError("provider_request_failure", f"{self.name} rejected the request (HTTP {response.status_code}); check credentials/model/structured-output support", 502)
                    return read(response)
        finally:
            # Cancellation while waiting takes precedence over success, parsing errors and fallback.
            check_cancelled()

    def complete(self, kind, messages, schema, run_id, fixing=False):
        """Return a ProviderResponse or raise AppError/httpx/ValueError; the router classifies the failure."""
        raise NotImplementedError
