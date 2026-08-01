import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ProductErrorBoundary } from "./components/ProductErrorBoundary";
import "./styles.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("ControlStudio root element was not found.");
}

createRoot(root).render(
  <StrictMode>
    <ProductErrorBoundary>
      <App />
    </ProductErrorBoundary>
  </StrictMode>,
);
