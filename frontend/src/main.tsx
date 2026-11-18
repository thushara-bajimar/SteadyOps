import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import "./index.css";
import Analytics from "./pages/Analytics";
import Approvals from "./pages/Approvals";
import FailureLab from "./pages/FailureLab";
import IncidentDetail from "./pages/IncidentDetail";
import Incidents from "./pages/Incidents";
import Overview from "./pages/Overview";
import Safety from "./pages/Safety";
import Services from "./pages/Services";

// Optional per-viewer theme override: ?theme=light or ?theme=dark (else follows the OS).
const theme = new URLSearchParams(window.location.search).get("theme");
if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/services" element={<Services />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/incidents/:id" element={<IncidentDetail />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/failure-lab" element={<FailureLab />} />
          <Route path="/safety" element={<Safety />} />
          <Route path="/analytics" element={<Analytics />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  </React.StrictMode>,
);
