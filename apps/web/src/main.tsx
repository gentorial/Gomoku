import { createRoot } from "react-dom/client";
import { App } from "./App.js";
import { initializeDiagnostics } from "./diagnostics.js";
import "./styles.css";

initializeDiagnostics();
createRoot(document.getElementById("root")!).render(<App />);
