import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./base.css";

// "/" is the public landing page and "/console" the operator console. Each loads as its own chunk, so the
// two stylesheets never meet on one page. nginx and Vite both fall back to index.html for any path.
const root = document.getElementById("root");
if (root) {
  const view: Promise<ReactNode> = window.location.pathname.startsWith("/console")
    ? import("./App").then(({ App }) => <App />)
    : import("./Landing").then(({ Landing }) => <Landing />);
  void view.then((node) => createRoot(root).render(<StrictMode>{node}</StrictMode>));
}
