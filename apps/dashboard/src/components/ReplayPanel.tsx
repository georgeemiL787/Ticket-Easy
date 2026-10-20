import { useState } from "react";
import { apiPost } from "../api/client";
import type { ReplayResult } from "../api/types/ReplayResult";
import { useAuth } from "../auth";
import { useI18n } from "../i18n";

function parse(text: string): Record<string, string> {
  const changes: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf("=");
    if (at <= 0) throw new Error(line);
    changes[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return changes;
}

/** Run the conversation again with other settings on a simulated shop (admins only) and see both runs side by side. */
export function ReplayPanel({ tenant, conversationId }: { tenant: string; conversationId: string }) {
  const { t } = useI18n();
  const auth = useAuth();
  const role = auth?.me.user?.role ?? null;
  const allowed = !auth?.me.auth_required || role === "admin";
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<ReplayResult | null>(null);
  if (!allowed) return null;

  async function run() {
    setProblem(null);
    setBusy(true);
    try {
      const changes = parse(text);
      setResult(await apiPost<ReplayResult>(`/v1/dashboard/conversations/${encodeURIComponent(conversationId)}/replay`, { tenant_id: tenant, changes }));
    } catch (error) {
      setProblem(error instanceof Error && !("code" in error) ? `${t("replay.badLine")}: ${error.message}` : String(error instanceof Error ? error.message : error));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="replay" aria-label={t("replay.title")}>
      <button type="button" className="link" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {t("replay.button")}
      </button>
      {open ? (
        <div className="replay-body">
          <p className="muted">{t("replay.explain")}</p>
          <label>
            <span>{t("replay.changes")}</span>
            <textarea rows={3} dir="ltr" value={text} placeholder="TEAM_B_AGENT_MODE=off" onChange={(e) => setText(e.target.value)} />
          </label>
          <button type="button" disabled={busy} onClick={run}>
            {busy ? t("replay.running") : t("replay.run")}
          </button>
          {problem ? <p role="alert">{problem}</p> : null}
          {result ? (
            <>
              <p>
                {t("replay.differ")}: {result.differing_turns.length} / {result.before.turns.length}
                {result.messages_from === "traces" ? ` · ${t("replay.hidden")}` : ""}
              </p>
              <table className="replay-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{t("replay.said")}</th>
                    <th>{t("replay.before")}</th>
                    <th>{t("replay.after")}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.before.turns.map((a, i) => {
                    const b = result.after.turns[i];
                    const differs = result.differing_turns.includes(i + 1);
                    return (
                      <tr key={i} className={differs ? "differs" : undefined}>
                        <td>{i + 1}</td>
                        <td dir="auto">{a.said}</td>
                        <td dir="auto">
                          <strong>{a.decision}</strong> {a.reply}
                          <div className="muted small">{a.moves.map((m) => m.kind).join(" → ")}</div>
                        </td>
                        <td dir="auto">
                          <strong>{b.decision}</strong> {b.reply}
                          <div className="muted small">{b.moves.map((m) => m.kind).join(" → ")}</div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
