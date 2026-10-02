import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashcashApp } from "./hashcash/HashcashApp";
import { initializeTheme } from "./theme";
import "./hashcash/hashcash.css";

initializeTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashcashApp />
  </StrictMode>,
);
