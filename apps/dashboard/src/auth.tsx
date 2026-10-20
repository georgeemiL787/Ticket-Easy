import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, apiGet, apiPost, setCsrfToken } from "./api/client";
import { useI18n } from "./i18n";

/** The signed-in person, as the service describes them (GET /v1/auth/me). */
export interface Me {
  auth_required: boolean;
  user: { user_id: string; email: string; display_name: string; role: "admin" | "manager" | "agent"; tenants: string[] } | null;
  csrf_token: string | null;
}

interface Auth {
  me: Me;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<Auth | null>(null);

export function useAuth(): Auth | null {
  return useContext(AuthContext);
}

function LoginForm({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await apiPost<Me>("/v1/auth/login", { email, password }));
    } catch (cause) {
      const status = cause instanceof ApiError ? cause.status : 0;
      setError(status === 401 ? t("login.wrong") : status === 429 ? t("login.slowDown") : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form onSubmit={submit} aria-labelledby="login-title">
        <h1 id="login-title">{t("login.title")}</h1>
        <label>
          <span>{t("login.email")}</span>
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          <span>{t("login.password")}</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error ? (
          <p role="alert" className="login-error">
            {error}
          </p>
        ) : null}
        <button type="submit" disabled={busy}>
          {t("login.submit")}
        </button>
      </form>
    </main>
  );
}

/** Shows the login page until the service says who is signed in. When the service has sign-in switched off, passes through. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [me, setMe] = useState<Me | null | "none">(null); // null: still asking, "none": nobody signed in

  const accept = useCallback((found: Me) => {
    setCsrfToken(found.csrf_token);
    setMe(found);
  }, []);

  useEffect(() => {
    apiGet<Me>("/v1/auth/me")
      .then(accept)
      .catch((cause) => {
        if (cause instanceof ApiError && cause.status === 401) setMe("none");
        else setMe({ auth_required: false, user: null, csrf_token: null }); // the pages show their own errors
      });
  }, [accept]);

  // A call that finds the session gone (expired, switched off) sends the person back to the login.
  useEffect(() => {
    const back = () => setMe("none");
    window.addEventListener("tb-signed-out", back);
    return () => window.removeEventListener("tb-signed-out", back);
  }, []);

  const signOut = useCallback(async () => {
    await apiPost("/v1/auth/logout", {}).catch(() => undefined);
    setCsrfToken(null);
    setMe("none");
  }, []);

  if (me === null) return <p className="state-title">{t("common.loading")}</p>;
  if (me === "none") return <LoginForm onSignedIn={accept} />;
  return <AuthContext.Provider value={{ me, signOut }}>{children}</AuthContext.Provider>;
}
