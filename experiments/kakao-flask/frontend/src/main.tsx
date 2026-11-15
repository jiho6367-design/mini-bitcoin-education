import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Root } from "./ui/Root";
import { initializeTheme } from "./theme";
import "./style.css";

initializeTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
