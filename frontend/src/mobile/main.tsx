import { createRoot } from "react-dom/client";
import "./theme.css";
import MobileApp from "./MobileApp";

createRoot(document.getElementById("mobile-root")!).render(<MobileApp />);
