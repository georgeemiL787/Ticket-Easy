from ..config import AppError
from ..diagnostics import contract_diagnostic, check_errors
from ..grounding import drop_stale_configuration
from ..llm.budget import input_fits
from .. import capabilities


class Runs:
    """Recorded model runs: every provider call starts a run that ends succeeded or failed with a redacted error."""

    def __init__(self, settings, store, providers):
        self.settings, self.store, self.providers = settings, store, providers

    def model_run(self, business_id, kind, payload, output):
        run = self.store.start_run(business_id, kind, payload)
        try:
            result = self.providers.call(kind, payload, output, run)
            return run, result
        except Exception as exc:
            self.fail_run(run, exc)
            raise

    def fail_run(self, run, exc):
        error = dict(code=exc.code if isinstance(exc, AppError) else "internal_error",message=str(exc) if isinstance(exc, AppError) else "Unexpected processing failure")
        if isinstance(exc, AppError) and "configuration_contract" in exc.details:
            secrets = self.settings.model_secrets
            error["details"] = {"configuration_contract": contract_diagnostic(exc.details["configuration_contract"], secrets), "errors": check_errors(exc.details.get("errors", []), secrets)}
            if "proposal_index" in exc.details:
                error["details"]["proposal_index"] = exc.details["proposal_index"]
            self.store.diagnostic(run, "grounding_error", error["details"])
        self.store.finish_run(run, error=error)
        if isinstance(exc, AppError):
            exc.details["run_id"] = run

    def tidy(self, run, content):
        content, stale = drop_stale_configuration(content)
        if stale:
            self.store.diagnostic(run, "stale_configuration_removed", dict(keys=stale))
        return content

    def checked_run(self, business_id, kind, payload, output_model, check):
        """A model run whose output must also pass `check`; a failed check fails the run."""
        run, output = self.model_run(business_id, kind, payload, output_model)
        try:
            result = check(output)
        except AppError as exc:
            self.fail_run(run, exc)
            raise
        self.store.finish_run(run, dict(items=len(result)))
        return run, result

    def fitted_index(self, kind, build, inventory, include_ids=None, exclude_ids=(), goal=""):
        """Operation index whose complete model input passes the provider size checks, shrinking the budget when needed.

        The owner's goal ranks the index, so the operations the request needs are not the ones a
        character budget happens to cut off.
        """
        budget = self.settings.capability_index_chars
        while True:
            index, coverage = capabilities.operation_index(inventory, budget, include_ids, exclude_ids, goal)
            payload = build(index, coverage)
            if input_fits(self.settings, kind, payload):
                return index, coverage, payload
            if len(index) <= 1:
                # Shrinking no longer helps: the fixed prompt alone exceeds the primary's allowance.
                # Send a request holding one operation so the chain can try a provider with more
                # room, instead of reporting that the capability is absent.
                raise AppError("model_input_limit", "The fixed prompt for this step exceeds the primary provider's input allowance; "
                              "reduce the business description or raise the primary provider's *_INPUT_TOKENS")
            budget = int(budget * 0.8)
