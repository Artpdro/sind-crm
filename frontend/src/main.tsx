import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Layout from "./Layout";
import Cadastros from "./pages/Cadastros";
import ChangePassword from "./pages/ChangePassword";
import Consultar from "./pages/Consultar";
import EmpresaForm from "./pages/EmpresaForm";
import EmpresaPerfil from "./pages/EmpresaPerfil";
import Empresas from "./pages/Empresas";
import Home from "./pages/Home";
import Importar from "./pages/Importar";
import Localizacao from "./pages/Localizacao";
import Mapa from "./pages/Mapa";
import Relatorios from "./pages/Relatorios";
import Login from "./pages/Login";
import { ForgotPassword, ResetPassword } from "./pages/Reset";
import "@fontsource-variable/montserrat";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "./styles.css";

// A proteção real é no backend; isto só evita mostrar telas que a API recusaria.
function Protected({ children }: { children: React.ReactElement }) {
  const { user, loading } = useAuth();
  if (loading) return <p className="muted">Carregando…</p>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.must_change_password) return <Navigate to="/trocar-senha" replace />;
  return children;
}
// Telas só para quem pode escrever / para admin (o backend valida de qualquer forma).
function Role({ roles, children }: { roles: string[]; children: React.ReactElement }) {
  const { user } = useAuth();
  return user && roles.includes(user.role) ? children : <Navigate to="/" replace />;
}
function NeedsSession({ children }: { children: React.ReactElement }) {
  const { user, loading } = useAuth();
  if (loading) return <p className="muted">Carregando…</p>;
  return user ? children : <Navigate to="/login" replace />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Layout>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/esqueci-senha" element={<ForgotPassword />} />
            <Route path="/redefinir-senha" element={<ResetPassword />} />
            <Route path="/trocar-senha" element={<NeedsSession><ChangePassword /></NeedsSession>} />
            <Route path="/" element={<Protected><Home /></Protected>} />
            <Route path="/empresas" element={<Protected><Empresas /></Protected>} />
            <Route path="/empresas/nova" element={<Protected><Role roles={["admin", "operador"]}><EmpresaForm /></Role></Protected>} />
            <Route path="/empresas/:id" element={<Protected><EmpresaPerfil /></Protected>} />
            <Route path="/empresas/:id/editar" element={<Protected><Role roles={["admin", "operador"]}><EmpresaForm /></Role></Protected>} />
            <Route path="/consultar" element={<Protected><Consultar /></Protected>} />
            <Route path="/importar" element={<Protected><Role roles={["admin"]}><Importar /></Role></Protected>} />
            <Route path="/mapa" element={<Protected><Mapa /></Protected>} />
            <Route path="/relatorios" element={<Protected><Relatorios /></Protected>} />
            <Route path="/localizacao" element={<Protected><Localizacao /></Protected>} />
            <Route path="/cadastros" element={<Protected><Role roles={["admin"]}><Cadastros /></Role></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Layout>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
