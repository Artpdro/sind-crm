import { useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import logo from "./assets/logo.png";
import logoBranca from "./assets/logo-branca.png";

const ROLE: Record<string, string> = { admin: "Administrador", operador: "Operador", visualizador: "Visualizador" };

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  return (
    <div className="shell">
      <header className={user ? "topbar" : "topbar sem-menu"}>
        <div className="container topbar-in">
          <Link to="/" className="brand" aria-label="Sindnorte — início">
            <img src={logo} alt="Sindnorte PE — Sindicato Empresarial do Sistema Comércio" width={150} height={58} />
          </Link>
          {user && (
            <>
              <button className="menu-btn" aria-expanded={open} aria-controls="menu" onClick={() => setOpen(!open)}>
                {open ? "Fechar" : "Menu"}
              </button>
              <nav id="menu" className={open ? "menu open" : "menu"}>
                <NavLink to="/" end={true} onClick={() => setOpen(false)}>Início</NavLink>
                <NavLink to="/empresas" end={true} onClick={() => setOpen(false)}>Empresas</NavLink>
                <NavLink to="/mapa" end={false} onClick={() => setOpen(false)}>Mapa</NavLink>
                <NavLink to="/consultar" end={false} onClick={() => setOpen(false)}>Consultar</NavLink>
                <NavLink to="/relatorios" end={false} onClick={() => setOpen(false)}>Relatórios</NavLink>
                {user.role !== "visualizador" && <NavLink to="/empresas/nova" end={false} onClick={() => setOpen(false)}>Cadastrar</NavLink>}
                {user.role !== "visualizador" && <NavLink to="/localizacao" end={false} onClick={() => setOpen(false)}>Localização</NavLink>}
                {user.role === "admin" && <NavLink to="/importar" end={false} onClick={() => setOpen(false)}>Importar</NavLink>}
                {user.role === "admin" && <NavLink to="/cadastros" end={false} onClick={() => setOpen(false)}>Cadastros</NavLink>}
                <span className="who"><strong>{user.username}</strong><br />{ROLE[user.role]}</span>
                <button className="btn btn-ghost" onClick={async () => { await logout(); nav("/login"); }}>Sair</button>
              </nav>
            </>
          )}
        </div>
      </header>
      <main className="container main">{children}</main>
      <footer className="footer">
        <div className="container footer-in">
          <div><img src={logoBranca} alt="Sindnorte PE" width={144} height={56} />Sindicato Empresarial do Sistema Comércio<br /><span style={{ opacity: .8 }}>CNC · Fecomércio · Sesc · Senac</span></div>
          <div>Sede: Rua Epitácio Pessoa, 04, Centro — Paulista, PE<br />Seg a Sex, 07:30–12:00 / 13:00–17:00</div>
          <div className="footer-fim">Sistema de gestão e mapa de empresas · uso interno · contém dados pessoais protegidos pela LGPD</div>
        </div>
      </footer>
    </div>
  );
}
