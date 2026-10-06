# Runbook: running Ticket-Easy day to day

For the person who looks after the service. No coding needed; every step is a command to copy or a page to click.
In the commands below, `team-b` is the service's name in Docker Compose (`team-b-full` if you started the full mode with
the AI model). Run them from the project's main folder. Without Docker, run the same `python -m team_b ...` commands in
`team_b/` with your settings in the environment.

Where to look first, whatever is wrong:
- `http://localhost:8010/health` answers "ok" when the service is up.
- **Dashboard, Alerts page:** the service raises an alert by itself when a part is down, answers are failing, or cases
  pile up. An alert shows when it opened and closes itself when the problem goes away.
  The alert names are: service down, action failing, unverified result, action missing, knowledge gap, slow replies,
  escalation spike, AI fallback, queue backlog. If `TEAM_B_ALERT_WEBHOOK` is set,
  the same alerts are sent there.
- `make logs` shows what the service is doing (one JSON line per event; personal data is hidden in it).

---

## 1. Switch off one action (for example refunds)

Use this when an action misbehaves, or the shop asks you to stop it for a while.

1. Open `team_b/config/tenants/<business>.json` (for the demo, `shop_001.json`).
2. In `"permissions"`, `"allowed_tools"`, delete the action's name (for refunds: `create_refund`). The other names are
   `create_return`, `create_exchange`, `cancel_order`, `update_delivery_address`, `apply_voucher`, `create_ticket`.
3. Restart so the change loads. With Docker the configuration is inside the image, so rebuild:
   `docker compose --profile light up -d --build` (use `--profile full` for the full mode).
4. Check with `GET /v1/capabilities?tenant_id=shop_001` while signed in as a manager (open it in the browser after
   signing in to the dashboard): the action should show as not allowed.

What customers then get: the agent does not do that action. It says it will pass the request to a person, and a case
appears in the inbox with the reason "capability missing", and the Alerts page shows an "action missing" alert. Nothing is changed in the shop. Nothing
else is affected.

To switch it back on, put the name back and restart the same way. A faster way, if the shop's own system can mark the
action as disabled, is to do that there: the agent notices within `TEAM_B_CAPABILITY_TTL_S` seconds (60 by default) and
stops using it with no restart.

There is no single button for this yet; it takes a configuration change and a restart (about a minute).

## 2. Switch the AI off (rules only)

The AI model only helps to understand messages and, if `TEAM_B_LLM_REWRITE=1`, to reword some replies. It never decides
what an action is allowed to do: those checks are plain rules and are the same with the AI on or off.

1. In your `.env` file set `TEAM_B_LLM=none` and `TEAM_B_LLM_REWRITE=0`.
2. Restart: `docker compose --profile light up -d` (the **light** profile has no AI model at all; stop the full one first
   with `make down`).
3. Check: the dashboard keeps working; replies come from the rules. Expect more "could you tell me more?" and more
   handoffs, especially in Arabic and Arabizi.

If the AI model only becomes slow or unreachable, you do not have to do anything: the agent falls back to the rules by
itself for those messages, and the Alerts page may show an "AI fallback" alert. Switch it off by hand only if it keeps flapping or costs too
much.

## 3. A pile-up of cases

You will see it as a "queue backlog" or "escalation spike" alert on the Alerts page, or a long list on **Escalations** or in `/inbox`.

1. Look at the cause on the dashboard: **Escalations** (grouped by reason) and **Alerts**.
   - Many "dependency unavailable": a part is down (shop, policy search, rule checker). Fix that first; see the Alerts
     page for which. When it is back, new conversations stop piling up. Existing cases still need a person.
   - Many "approval required": a manager must decide. Open them from Escalations and approve or reject.
   - Many "no evidence" or "low confidence" on the same topic: the knowledge is missing. Look at the **Unanswered** page
     and add the policy text.
2. Add people: see section 7. A person with the agent role can claim and answer. A manager can reassign cases to
   others ("assign").
3. Work the most urgent first: the list is sorted by priority and then by age. Claim a case (it is yours), reply, then
   resolve it, or "give back to the assistant" when the customer's problem is solved.
4. If the flood comes from one customer or a bot, the service already limits each conversation to
   `TEAM_B_RATE_LIMIT_PER_MIN` messages a minute (20 by default). Lower it, or give the business an API key so the
   chat only accepts your own website (`TEAM_B_CHAT_API_KEYS="shop_001=<secret>"`, then your site sends `X-Api-Key`).

Cases are never deleted while they are open or claimed, so a pile-up does not lose anything.

## 4. An unverified result

This means the shop said "done" but the agent could not prove it (no reference number, or the shop's answer is about a
different order or amount). The agent then tells the customer that a person will confirm, and opens a case with the
reason **unverified result**.

1. Open the case in Escalations (or `/inbox`) and claim it.
2. Read the briefing: it names the action, the order, the amount and what the shop answered.
3. Check in the shop's own system whether the change really happened.
   - It happened: reply to the customer to confirm and resolve the case.
   - It did not happen: do it by hand in the shop (do not press the action again from here), then reply and resolve.
   - You cannot tell: ask the shop's team before telling the customer anything.
4. Do not retry the same action blindly. The agent never repeats a change it could not verify, because that could
   refund twice.
5. If it happens often, something is wrong in the shop's answers: look at the dashboard **Actions** page (what was
   sent to the shop and what came back), and tell the shop team. The Alerts page shows an "unverified result" alert.

## 5. Back up and restore

Everything the service keeps (conversations, decision logs, cases, people who sign in, alerts) is one SQLite file:
`/data/team_b.sqlite3` in the Docker volume `team_b_data` (or `TEAM_B_DB_PATH`). **A backup holds personal data:** keep it
as protected as the service, and delete old ones on the same schedule as `TEAM_B_RETENTION_DAYS`.

Back up (safe while the service runs):
```
docker compose exec team-b python -c "import sqlite3; s=sqlite3.connect('/data/team_b.sqlite3'); d=sqlite3.connect('/data/backup.sqlite3'); s.backup(d); d.close()"
docker compose cp team-b:/data/backup.sqlite3 ./backup-$(date +%F).sqlite3
docker compose exec team-b rm /data/backup.sqlite3
```
Do this daily; the file is small.

Restore:
1. `make down` (stops the service, keeps the volume).
2. Put the backup into the volume: `docker compose run --rm --no-deps -v "$(pwd):/backup" team-b cp /backup/backup-YYYY-MM-DD.sqlite3 /data/team_b.sqlite3`
   (the exact command may need small changes for your folders and shell).
3. `docker compose --profile light up -d`, then open the dashboard and check the newest conversation is the one you
   expect, and that you can sign in. Anything after the backup's time is gone.

`make reset` **deletes** the volume and everything in it. It is not a restore.

This procedure was written from the file layout; it has not been practised with a real Docker daemon yet. Practise it
once on the demo before you rely on it.

## 6. Rotate secrets

| Secret | Where it lives | How to change it | What it affects |
|---|---|---|---|
| `TEAM_B_SECRET_KEY` (signs the sign-in cookie) | `.env` | put a new long random value (for example 40 random characters), restart | everybody is signed out and signs in again |
| A person's password | the database | `docker compose exec team-b python -m team_b create-user --email <their email> --name <their name> --role <role> --tenants <business> --generate-password --reset-password`, then give them the password printed once | only that person (an existing signed-in session lasts until it expires, up to `TEAM_B_SESSION_HOURS`; to cut it at once, also rotate the secret key) |
| `TEAM_B_CHAT_API_KEYS` | `.env` | change the value, restart, update your website at the same time | the customer chat of that business refuses calls with the old key |
| `OPENROUTER_API_KEY` | `.env` | create the new key at the provider, put it in, restart, delete the old key at the provider | only the AI model calls |
| `TEAM_B_ALERT_WEBHOOK` (the address contains a secret) | `.env` | make a new webhook address, put it in, restart | alert delivery |

Never put a secret in a file that is committed. `.env` is ignored by git; check with `git status` that it is not listed.
If a secret was ever committed or pasted in a chat, treat it as leaked and rotate it now.

## 7. Add (or remove) a support person

Add:
```
docker compose exec team-b python -m team_b create-user --email sara@example.com --name "Sara" --role agent --tenants shop_001 --generate-password
```
- `--role`: `agent` (answers cases), `manager` (also approves refunds, reassigns, sees the dashboard), `admin` (everything,
  every business, set up the others).
- `--name` is the name shown on cases. It must be unique, because it is the name recorded in the history of every case
  the person touches.
- `--tenants`: the businesses they may see (an admin sees all). A person sees nothing of another business.
- The password is printed once; hand it over safely and ask the person to keep it. At least 10 characters if you choose
  your own with `--password` (but that stays in your shell history, so prefer `--generate-password`).

Change someone's role or businesses: not possible from the command line yet. Ask a developer (the store supports it, the
command does not).

Remove someone: **not possible from the command line yet** (the service can switch an account off, and a switched-off
account is refused at once, but no command calls it). Until it exists: reset their password with
`--generate-password --reset-password` and do not pass it on, then rotate `TEAM_B_SECRET_KEY` (section 6) to end any
session they have open. Ask a developer to add the command.

---

## When to ask a developer
- The service does not start, or `/health` does not answer after a restart.
- An alert that does not close itself after the cause is fixed.
- A case where the shop shows a change the agent says it did not make (or the reverse) and the explanation above does
  not match. Keep the case open and note its case id and conversation id.
- Anything that looks like one customer seeing another customer's data. Switch the chat off at once (stop the service
  with `make down`), then call a developer.
