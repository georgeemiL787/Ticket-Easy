from .services.capability import Capability
from .services.connectors import Connectors
from .services.proposals import CLOSED, change_summary, Proposals
from .services.runs import Runs
from .services.discovery import Discovery
from .services.generation import Generation
from .services.requests import Requests
from .services.review import Review
from .services.building import Building
from .services.publication import Publication


class Service:
    """Facade over the workflow services; callers (web, MCP, scripts, tests) keep this one entry point."""

    def __init__(self, settings, store, providers):
        self.settings, self.store = settings, store
        self.runs = Runs(settings, store, providers)
        self.discovery = Discovery(settings, store, providers, self.runs)
        self.proposals = Proposals(settings, store, providers, self.discovery)
        self.generation = Generation(settings, store, providers, self.runs, self.discovery, self.proposals)
        self.requests = Requests(settings, store, providers, self.runs, self.discovery, self.proposals, self.generation)
        self.review = Review(settings, store, providers, self.runs, self.discovery, self.proposals)
        self.building = Building(settings, store, providers, self.discovery, self.proposals, self.review)
        self.publishing = Publication(settings, store, providers, self.building)
        self.capability = Capability(settings, store, self.proposals, self.discovery)
        self.connectors = Connectors(settings, store, self.proposals, self.discovery)
        self.providers = providers

    # Callers replace these on the facade; every service must see the same objects.
    @property
    def providers(self):
        return self._providers

    @providers.setter
    def providers(self, value):
        self._providers = value
        for s in (self.runs, self.discovery, self.proposals, self.generation, self.requests, self.review, self.building, self.publishing):
            s.providers = value

    @property
    def fetch_transport(self):
        return self.discovery.fetch_transport

    @fetch_transport.setter
    def fetch_transport(self, value):
        self.discovery.fetch_transport = value

    @property
    def execution_transport(self):
        return self.building.execution_transport

    @execution_transport.setter
    def execution_transport(self, value):
        self.building.execution_transport = value

    @property
    def organizing(self):
        return self.discovery.organizing

    snapshot = staticmethod(Proposals.snapshot)
    check_current = staticmethod(Proposals.check_current)
    settle = staticmethod(Proposals.settle)
    scoped = staticmethod(Proposals.scoped)

    def business(self, name, description): return self.discovery.business(name, description)
    def upload(self, business_id, filename, raw): return self.discovery.upload(business_id, filename, raw)
    def fetch(self, business_id, url): return self.discovery.fetch(business_id, url)
    def _fetch_url(self, url, allowed): return self.discovery._fetch_url(url, allowed)
    def _fetch_get(self, url): return self.discovery._fetch_get(url)
    def save_openapi(self, business_id, filename, parse_as, raw, source): return self.discovery.save_openapi(business_id, filename, parse_as, raw, source)
    def spec(self, spec_id): return self.discovery.spec(spec_id)
    def local_project(self, business_id, path, setting_values=None): return self.discovery.local_project(business_id, path, setting_values)
    def save_code_spec(self, business_id, filename, document, inventory, cache_key): return self.discovery.save_code_spec(business_id, filename, document, inventory, cache_key)
    def analyze_code(self, spec_id): return self.discovery.analyze_code(spec_id)
    def request_spec(self, business_id, spec_id=None): return self.discovery.request_spec(business_id, spec_id)
    def area_row(self, spec_id): return self.discovery.area_row(spec_id)
    def areas(self, spec_id): return self.discovery.areas(spec_id)
    def area_scope(self, spec_id): return self.discovery.area_scope(spec_id)
    def organize_areas(self, spec_id): return self.discovery.organize_areas(spec_id)
    def _organize_areas(self, spec_id, spec): return self.discovery._organize_areas(spec_id, spec)
    def select_areas(self, spec_id, submission): return self.discovery.select_areas(spec_id, submission)

    def view(self, pid, version=None): return self.proposals.view(pid, version)
    def current_contents(self, business_id): return self.proposals.current_contents(business_id)
    def existing_tools(self, business_id): return self.proposals.existing_tools(business_id)

    def model_run(self, business_id, kind, payload, output): return self.runs.model_run(business_id, kind, payload, output)
    def fail_run(self, run, exc): return self.runs.fail_run(run, exc)
    def tidy(self, run, content): return self.runs.tidy(run, content)
    def checked_run(self, business_id, kind, payload, output_model, check): return self.runs.checked_run(business_id, kind, payload, output_model, check)
    def fitted_index(self, kind, build, inventory, include_ids=None, exclude_ids=()): return self.runs.fitted_index(kind, build, inventory, include_ids, exclude_ids)

    def generate(self, spec_id, operation_ids=None, request=None): return self.generation.generate(spec_id, operation_ids, request)
    def generation_attempt(self, spec, scope, payload, request): return self.generation.generation_attempt(spec, scope, payload, request)
    def generation_used(self, spec_id): return self.generation.generation_used(spec_id)
    def next_generation_batch(self, spec_id): return self.generation.next_generation_batch(spec_id)

    def tool_request(self, rid): return self.requests.tool_request(rid)
    def tool_requests(self, business_id): return self.requests.tool_requests(business_id)
    def request_tool(self, business_id, submission, source="owner", suggestion_id=None, scope=None, clarifications=()): return self.requests.request_tool(business_id, submission, source, suggestion_id, scope, clarifications)
    def process_request(self, rid, exclude_ids=()): return self.requests.process_request(rid, exclude_ids)
    def save_request(self, rid, status, runs, unresolved, coverage, fields): return self.requests.save_request(rid, status, runs, unresolved, coverage, fields)
    def clarify_request(self, rid, submission): return self.requests.clarify_request(rid, submission)
    def request_next_batch(self, rid): return self.requests.request_next_batch(rid)
    def suggestion(self, sid): return self.requests.suggestion(sid)
    def suggestions(self, business_id): return self.requests.suggestions(business_id)
    def suggest(self, business_id, submission): return self.requests.suggest(business_id, submission)
    def decide_suggestion(self, sid, submission): return self.requests.decide_suggestion(sid, submission)

    def answers(self, pid, version, submission): return self.review.answers(pid, version, submission)
    def reconcile(self, pid, version, revision): return self.review.reconcile(pid, version, revision)
    def rejected_revision(self, result, spec, scope): return self.review.rejected_revision(result, spec, scope)
    def decide(self, pid, version, submission): return self.review.decide(pid, version, submission)
    def revise(self, pid, submission, constraint=None, actor=None, repair=False): return self.review.revise(pid, submission, constraint, actor, repair)
    def confirm_requirement(self, pid, version, requirement_id, submission): return self.review.confirm_requirement(pid, version, requirement_id, submission)
    def dismiss_gap(self, pid, version, gap_index, submission): return self.review.dismiss_gap(pid, version, gap_index, submission)
    def supersede_question(self, pid, version, question_id, submission): return self.review.supersede_question(pid, version, question_id, submission)

    def approval(self, pid, version, content_sha256=None): return self.building.approval(pid, version, content_sha256)
    def approved_context(self, pid, connector_id): return self.building.approved_context(pid, connector_id)
    def enforcement(self, eid): return self.building.enforcement(eid)
    def current_enforcement(self, pid, version): return self.building.current_enforcement(pid, version)
    def submit_enforcement(self, pid, submission): return self.building.submit_enforcement(pid, submission)
    def review_enforcement(self, eid, submission): return self.building.review_enforcement(eid, submission)
    def check_enforcement(self, content): return self.building.check_enforcement(content)
    def build_artifact(self, pid, submission): return self.building.build_artifact(pid, submission)
    def artifact(self, aid): return self.building.artifact(aid)
    def run_sandbox(self, aid, submission, mode="sandbox", audit=None, precheck=None): return self.building.run_sandbox(aid, submission, mode, audit, precheck)
    def run_sandbox_test(self, aid, submission): return self.building.run_sandbox_test(aid, submission)
    def check_scenario(self, a, submission): return self.building.check_scenario(a, submission)
    def record_selectors(self, a): return self.building.record_selectors(a)
    def repair(self, test_id, submission): return self.building.repair(test_id, submission)

    def publication(self, pub_id): return self.publishing.publication(pub_id)
    def publication_view(self, row): return self.publishing.publication_view(row)
    def publications(self, business_id=None, artifact_id=None): return self.publishing.publications(business_id, artifact_id)
    def publication_problems(self, aid): return self.publishing.publication_problems(aid)
    def publish(self, aid, submission): return self.publishing.publish(aid, submission)
    def disable_publication(self, pub_id, submission): return self.publishing.disable_publication(pub_id, submission)
    def check_publication(self, pub): return self.publishing.check_publication(pub)
    def published_tools(self, business_id, identity): return self.publishing.published_tools(business_id, identity)
    def invoke_published(self, business_id, identity, name, arguments): return self.publishing.invoke_published(business_id, identity, name, arguments)

    def accept_policy(self, pid, version, submission): return self.capability.accept(pid, version, submission)
    def reopen_policy(self, pid, version, submission): return self.capability.reopen(pid, version, submission)
    def run_policy_tests(self, aid): return self.capability.tests(self.artifact(aid))
