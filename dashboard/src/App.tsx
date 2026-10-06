import { Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { Layout } from "./components/Layout";
import { I18nProvider, isLang } from "./i18n";
import Actions from "./pages/Actions";
import Alerts from "./pages/Alerts";
import ConversationDetail from "./pages/ConversationDetail";
import Conversations from "./pages/Conversations";
import Escalations from "./pages/Escalations";
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
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="conversations" element={<Conversations />} />
          <Route path="conversations/:id" element={<ConversationDetail />} />
          <Route path="escalations" element={<Escalations />} />
          <Route path="actions" element={<Actions />} />
          <Route path="unanswered" element={<Unanswered />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </WithLanguage>
  );
}
