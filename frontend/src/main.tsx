import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("No se encontró el nodo raíz de la aplicación.");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
