import type { ReactNode } from "react";
import { useAuth } from "../../auth";
import { LightPage } from "../../components/LightPage";
import { useI18n } from "../../i18n";

/** Setting up a business is an administrator's job: anyone else gets a clear message, not a failed request. */
export function AdminOnly({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const auth = useAuth();
  const user = auth?.me.user;
  if (user && user.role !== "admin") {
    return (
      <LightPage title={t("ob.title")} intro={t("ob.intro")}>
        <p role="status" className="muted">
          {t("ob.adminOnly")}
        </p>
      </LightPage>
    );
  }
  return <>{children}</>;
}
