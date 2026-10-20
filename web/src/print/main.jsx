import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { GlossaryProvider } from "../components/Hint.jsx";
import icons from "../lib/icons.js";
import L from "../lib/logo.js";
import Print from "./Print.jsx";
import "../styles/system.css";
import "../styles/print.css";
import "../styles/brand-gpb.css";

icons.sprite();
L.favicon();
createRoot(document.getElementById("root")).render(<StrictMode><GlossaryProvider><Print /></GlossaryProvider></StrictMode>);
