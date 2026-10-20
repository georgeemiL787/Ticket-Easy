import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
// The opt-in light theme: one source shared with the chat and inbox pages (nothing applies until data-theme="aurora-light" is set).
import "../../../services/agent/web/shared/aurora-light.css";

// The service serves this app under /dashboard (vite's `base`), so the router starts there.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename="/dashboard">
      <App />
    </BrowserRouter>
  </StrictMode>,
);
