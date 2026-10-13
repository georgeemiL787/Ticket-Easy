import { Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { AuthGate } from "./auth";
import { Layout } from "./components/Layout";
import { I18nProvider, isLang } from "./i18n";
import Actions from "./pages/Actions";
import Alerts from "./pages/Alerts";
import BusinessTools from "./pages/BusinessTools";
import Connect from "./pages/tools/Connect";
import Publish from "./pages/tools/Publish";
import Review from "./pages/tools/Review";
import ConversationDetail from "./pages/ConversationDetail";
import Conversations from "./pages/Conversations";
import Escalations from "./pages/Escalations";
import Guardrails from "./pages/Guardrails";
import Knowledge from "./pages/Knowledge";
import OnboardingFlow from "./pages/onboarding/OnboardingFlow";
import OnboardingList from "./pages/onboarding/OnboardingList";
import { AdminOnly } from "./pages/onboarding/AdminOnly";
import Overview from "./pages/Overview";
import Unanswered from "./pages/Unanswered";

/** The display language lives in the address (?lang=ar), so a shared link opens in the same language and direction. */
function WithLanguage({ children }: { children: React.ReactNode }) {
  const [params] = useSearchParams();
  const value = params.get("lang");
  return <I18nProvider lang={isLang(value) ? value : "en"}>{children}</I18nProvider>;
}

export default function App() {
  return (
    <WithLanguage>
      <AuthGate>
        <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="conversations" element={<Conversations />} />
          <Route path="conversations/:id" element={<ConversationDetail />} />
          <Route path="escalations" element={<Escalations />} />
          <Route path="actions" element={<Actions />} />
          <Route path="unanswered" element={<Unanswered />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="onboarding" element={<AdminOnly><OnboardingList /></AdminOnly>} />
          <Route path="onboarding/:id" element={<AdminOnly><OnboardingFlow /></AdminOnly>} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route path="guardrails" element={<Guardrails />} />
          <Route path="tools" element={<BusinessTools />} />
          <Route path="tools/connect" element={<Connect />} />
          <Route path="tools/review" element={<Review />} />
          <Route path="tools/publish" element={<Publish />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
        </Routes>
      </AuthGate>
    </WithLanguage>
  );
}
