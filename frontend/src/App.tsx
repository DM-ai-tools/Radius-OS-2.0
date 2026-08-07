import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import LandingPage from "./pages/LandingPage";
import ClientsPage from "./pages/ClientsPage";
import ChatPage from "./pages/ChatPage";
import { useAuth } from "./auth";
import "./landing.css";

function RequireAuth({ children }: { children: ReactNode }) {
  const { token, ready } = useAuth();
  if (!ready) {
    return (
      <div className="page" style={{ padding: 48 }}>
        Loading…
      </div>
    );
  }
  if (!token) {
    return <Navigate to="/?auth=login" replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <div className="app-shell">
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route
          path="/app"
          element={
            <RequireAuth>
              <ClientsPage />
            </RequireAuth>
          }
        />
        <Route path="/clients" element={<Navigate to="/app" replace />} />
        <Route
          path="/clients/:clientId/chat"
          element={
            <RequireAuth>
              <ChatPage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
