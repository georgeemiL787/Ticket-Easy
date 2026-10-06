"""Command line: python -m team_b eval-nlu | eval --set eval/conversations | seed-demo | create-user"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from team_b import eval_conversations as convo
from team_b.brain.llm_nlu import LLMNLU
from team_b.brain.nlu import NLU, RuleBasedNLU
from team_b.config import Settings
from team_b.container import Container, build_llm
from team_b.demo_seed import DriftClock
from team_b.domain.tenant import TenantRegistry
from team_b.judge import Judge, agreement, pick_spotcheck, write_spotcheck
from team_b.nlu_eval import LABELLED_PATH, EvalReport, evaluate, load_labelled, render, save_baseline


def build_nlu(mode: str, settings: Settings) -> NLU:
    """The understanding to measure: the rules alone, or the AI-assisted one when an AI model is configured."""
    if mode == "rules":
        return RuleBasedNLU()
    llm = build_llm(settings)
    if llm is None:
        raise SystemExit("--mode llm needs an AI model: set TEAM_B_LLM=ollama or openrouter (see .env.example).")
    return LLMNLU(llm)


async def eval_nlu(mode: str, tenant_id: str, data: Path, save: bool) -> list[EvalReport]:
    settings = Settings.from_env()
    tenant = TenantRegistry.from_dir(settings.config_dir).get(tenant_id)
    messages = load_labelled(data)
    reports = []
    for one in ("rules", "llm") if mode == "both" else (mode,):
        report = await evaluate(build_nlu(one, settings), tenant, messages, mode=one)
        reports.append(report)
        print(render(report))
        print()
        if save:
            save_baseline(report)
    return reports


async def eval_conversations(directory: Path, use_llm: bool, out: Path, save: bool, use_judge: bool = False) -> int:
    settings = Settings.from_env()
    if use_llm and settings.llm == "none":
        raise SystemExit("--llm needs an AI model: set TEAM_B_LLM=ollama or openrouter (see .env.example).")
    if not use_llm:
        settings = settings.model_copy(update={"llm": "none", "llm_rewrite": False})
    judge = None
    if use_judge:
        llm = build_llm(Settings.from_env())
        if llm is None:
            raise SystemExit("--judge needs an AI model: set TEAM_B_LLM=ollama or openrouter (see .env.example).")
        judge = Judge(llm)
    conversations = convo.load_set(directory)
    mode = "llm" if use_llm else "rules"
    report = await convo.evaluate(conversations, settings, mode=mode, judge=judge)
    print(convo.render(report))
    markdown, data = convo.write_report(report, out)
    print(f"wrote {markdown} and {data}")
    if judge is not None:
        spot = pick_spotcheck(report.judged)
        path = out / "judge_spotcheck.csv"
        print(f"wrote {write_spotcheck(spot, path)} replies to {path} for a person to grade")
    if save:
        if use_llm:
            raise SystemExit("the baseline is recorded from the rules (run without --llm)")
        small = await convo.evaluate(convo.sample(conversations, 5), settings, mode=mode)
        convo.save_baseline(report, small)
        print(f"recorded the baseline in {convo.BASELINE_PATH}")
    return 0


async def seed_demo(conversations: int, days: int, force: bool) -> int:
    """Fill the SQLite database with synthetic conversations for the dashboard, unless it already has some.

    The timeline ends at the service's own "now" (the fixed demo day if TEAM_B_FIXED_TODAY is set, else today), so the
    dashboard's windows contain it. Two days from the end a short outage of the policy search is played with the
    alert engine watching: the Alerts page then shows a resolved "service down"; what holds at the end stays open."""
    import logging
    from datetime import UTC, datetime, time, timedelta

    from team_b.container import build_container
    from team_b.demo_seed import TENANT, DriftClock, seed
    from team_b.observability import configure_logging

    settings = Settings.from_env().model_copy(update={"store": "sqlite"})
    configure_logging(json_logs=False, level=logging.WARNING)
    end = datetime.combine(settings.fixed_today, time(12, 0), tzinfo=UTC) if settings.fixed_today else datetime.now(UTC)
    clock = DriftClock(end - timedelta(days=days))
    container = build_container(settings, clock=clock)
    if not force and await container.traces.query(TENANT, limit=1):
        print(f"{settings.db_path} already has conversations: nothing seeded (use --force to add more)")
        return 0
    earlier = int(conversations * 0.85)
    first = await seed(container, clock, conversations=earlier, days=max(1, days - 2), prefix="seed")
    await outage_incident(container, clock)
    second = await seed(container, clock, conversations=conversations - earlier, days=2, seed=2, prefix="late")
    if container.alert_engine is not None:
        await container.alert_engine.evaluate(TENANT)
        opened = [a.rule for a in await container.alerts.list(TENANT, open_only=True)] if container.alerts else []
        print("open alerts:", ", ".join(opened) or "none")
    print(f"seeded {first.conversations + second.conversations} conversations, {first.cases + second.cases} cases")
    print(f"database: {settings.db_path}")
    return 0


async def outage_incident(container: "Container", clock: "DriftClock") -> None:
    """The policy search is down for a few minutes while customers ask questions, then it recovers."""
    from team_b.demo_seed import TENANT

    assert container.policy_search is not None and container.orchestrator is not None
    container.policy_search.fail_next("search_knowledge", 1000)
    for i in range(6):
        await container.orchestrator.handle_turn(TENANT, f"incident-{i}", "What is your return policy?")
        clock.advance(seconds=20)
    if container.alert_engine is not None:
        await container.alert_engine.evaluate(TENANT)  # service down opens
    container.policy_search.reset()
    clock.advance(minutes=10)
    for i in range(3):
        await container.orchestrator.handle_turn(TENANT, f"recovered-{i}", "What is your return policy?")
        clock.advance(seconds=30)
    if container.alert_engine is not None:
        await container.alert_engine.evaluate(TENANT)  # and resolves


async def create_user_cmd(args: argparse.Namespace) -> int:
    """Add a person who may sign in (or, with --reset-password, set a new password for an existing one)."""
    import getpass
    import secrets

    from team_b.auth import WeakPasswordError, hash_password
    from team_b.container import build_container
    from team_b.ports import AlreadyExistsError

    settings = Settings.from_env().model_copy(update={"store": "sqlite"})
    container = build_container(settings)
    assert container.auth is not None and container.users is not None
    known = set(container.tenants.tenant_ids())
    tenants = tuple(t.strip() for t in args.tenants.split(",") if t.strip())
    if unknown := [t for t in tenants if t not in known]:
        print(f"unknown business(es): {', '.join(unknown)} (known: {', '.join(sorted(known))})")
        return 1
    if args.role != "admin" and not tenants:
        print("a manager or agent needs at least one business: --tenants shop_001")
        return 1
    existing = await container.users.get_with_hash(args.email)
    if existing is not None and args.if_missing and not args.reset_password:
        print(f"{args.email} already exists: nothing changed")
        return 0
    password = args.password
    generated = False
    if password is None and args.generate_password:
        password, generated = secrets.token_urlsafe(12), True
    if password is None:
        password = getpass.getpass("Password (at least 10 characters): ")
    try:
        if existing is not None:
            if not args.reset_password:
                print(f"{args.email} already exists (use --reset-password to set a new password)")
                return 1
            await container.users.set_password(existing[0].user_id, hash_password(password))
            print(f"password changed for {args.email}")
        else:
            user = await container.auth.create_user(args.email, args.name, args.role, tenants, password)
            print(
                f"created {user.role} {user.display_name} <{user.email}> for: "
                f"{', '.join(user.tenants) or 'every business'}"
            )
    except WeakPasswordError as exc:
        print(str(exc))
        return 1
    except AlreadyExistsError as exc:
        print(f"cannot create: {exc}")
        return 1
    if generated:
        print(f"password: {password}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m team_b", description="Ticket-Easy Team B tools")
    commands = parser.add_subparsers(dest="command", required=True)
    nlu = commands.add_parser("eval-nlu", help="measure understanding accuracy on the labelled messages")
    nlu.add_argument("--mode", choices=["rules", "llm", "both"], default="rules")
    nlu.add_argument("--tenant", default="shop_001")
    nlu.add_argument("--data", type=Path, default=LABELLED_PATH, help="labelled messages (JSON lines)")
    nlu.add_argument("--save-baseline", action="store_true", help="record the intent accuracy as the new baseline")
    full = commands.add_parser("eval", help="measure the whole agent on the conversation set, per language style")
    full.add_argument("--set", type=Path, default=convo.CONVERSATIONS_DIR, help="folder of conversations (JSON lines)")
    full.add_argument("--llm", action="store_true", help="use the configured AI model for understanding")
    full.add_argument("--out", type=Path, default=Path("reports"), help="where the report files go")
    full.add_argument(
        "--judge", action="store_true", help="also grade how the replies read with the AI judge (advice only)"
    )
    full.add_argument("--save-baseline", action="store_true", help="record the rules' result as the baseline")
    agree = commands.add_parser(
        "judge-agreement", help="compare the judge with the person who graded judge_spotcheck.csv"
    )
    agree.add_argument("file", type=Path)
    seed = commands.add_parser("seed-demo", help="fill the database with synthetic conversations for the dashboard")
    seed.add_argument("--conversations", type=int, default=150)
    seed.add_argument("--days", type=int, default=14)
    seed.add_argument("--force", action="store_true", help="add conversations even if the database already has some")
    user = commands.add_parser("create-user", help="add a person who may sign in to the inbox and the dashboard")
    user.add_argument("--email", required=True)
    user.add_argument("--name", required=True, help="the name shown in the inbox (unique)")
    user.add_argument("--role", choices=["agent", "manager", "admin"], default="agent")
    user.add_argument("--tenants", default="", help="businesses this person may see, comma separated (admin: all)")
    user.add_argument("--password", help="avoid on a shared computer: it stays in the shell history")
    user.add_argument("--generate-password", action="store_true", help="make a random password and print it once")
    user.add_argument("--if-missing", action="store_true", help="do nothing when the email already exists")
    user.add_argument("--reset-password", action="store_true", help="set a new password for an existing email")
    args = parser.parse_args(argv)
    if args.command == "create-user":
        return asyncio.run(create_user_cmd(args))
    if args.command == "eval":
        return asyncio.run(eval_conversations(args.set, args.llm, args.out, args.save_baseline, args.judge))
    if args.command == "judge-agreement":
        result = agreement(args.file)
        print(json.dumps(result.as_dict(), indent=2))
        return 0
    if args.command == "seed-demo":
        return asyncio.run(seed_demo(args.conversations, args.days, args.force))
    asyncio.run(eval_nlu(args.mode, args.tenant, args.data, args.save_baseline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
