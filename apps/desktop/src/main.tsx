import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { DesktopDownloads } from "./DesktopDownloads";
import "./styles.css";
import "./ui-scale.css";
import "./ui-responsive.css";
import "./gateway-settings.css";
import "./ui-workspaces.css";
import "./creator-ui.css";
import "./footage-batches.css";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Root element #root was not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
    <DesktopDownloads />
  </StrictMode>,
);
