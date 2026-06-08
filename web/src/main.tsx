import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import "./index.css";
import { AuthProvider, RequireAuth } from "./auth";
import { Login } from "./pages/Login";
import { SetupAdmin } from "./pages/SetupAdmin";
import { Overview } from "./pages/Overview";
import { PersonPage } from "./pages/Person";
import { Settings } from "./pages/Settings";
import { Roles } from "./pages/admin/Roles";
import { Users } from "./pages/admin/Users";
import { Managers } from "./pages/admin/Managers";
import { Machines } from "./pages/admin/Machines";
import { TaxonomyRules } from "./pages/admin/TaxonomyRules";
import { WorkflowTemplates } from "./pages/admin/WorkflowTemplates";
import { EmployeeSetup } from "./pages/EmployeeSetup";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/setup-admin" element={<SetupAdmin />} />
          <Route path="/setup" element={<EmployeeSetup />} />
          <Route path="/" element={<RequireAuth><Overview /></RequireAuth>} />
          <Route path="/person/:uid" element={<RequireAuth><PersonPage /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth admin><Settings /></RequireAuth>} />
          <Route path="/admin/roles" element={<RequireAuth admin><Roles /></RequireAuth>} />
          <Route path="/admin/users" element={<RequireAuth admin><Users /></RequireAuth>} />
          <Route path="/admin/managers" element={<RequireAuth admin><Managers /></RequireAuth>} />
          <Route path="/admin/machines" element={<RequireAuth admin><Machines /></RequireAuth>} />
          <Route path="/admin/taxonomy-rules" element={<RequireAuth admin><TaxonomyRules /></RequireAuth>} />
          <Route path="/admin/workflow-templates" element={<RequireAuth admin><WorkflowTemplates /></RequireAuth>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
