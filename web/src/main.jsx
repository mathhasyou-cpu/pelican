import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import icons from "./lib/icons.js";
import L from "./lib/logo.js";
import "./styles/system.css";
import "./styles/app.css";
import "./styles/screens.css";
import "./styles/brand-gpb.css";

icons.sprite();
L.favicon();
createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
