import L from "leaflet";
import "leaflet.markercluster";
import { useEffect, useRef } from "react";
import { api } from "../api";
import type { Bbox, Marcador } from "../types";

type Detalhe = { id: number; nome: string; nome_fantasia: string | null; cnpj: string | null; rua: string | null; numero: string | null; complemento: string | null;
  bairro: string | null; cidade: string | null; estado: string | null; cep: string | null; situacao_nome: string | null; porte_nome: string | null;
  tipo_nome: string | null; categorias: { nome: string }[] };
type Props = { marcadores: Marcador[]; selecionado: { id: number; n: number } | null; ajustar: number; onVer: (id: number) => void; onMover?: (b: Bbox) => void };

const cnpjFmt = (c: string | null) => (c && c.length === 14 ? `${c.slice(0, 2)}.${c.slice(2, 5)}.${c.slice(5, 8)}/${c.slice(8, 12)}-${c.slice(12)}` : c);

// O conteúdo do popup é montado com textContent (nunca innerHTML): dados de empresas jamais são interpretados como HTML.
function linha(rotulo: string, valor: string | null | undefined) {
  const p = document.createElement("p"); p.style.margin = "2px 0";
  const b = document.createElement("strong"); b.textContent = rotulo + ": ";
  p.append(b, document.createTextNode(valor || "—")); return p;
}
function montarPopup(d: Detalhe, onVer: (id: number) => void) {
  const box = document.createElement("div");
  const t = document.createElement("strong"); t.textContent = d.nome; box.append(t);
  const fantasia = d.nome_fantasia ? linha("Fantasia", d.nome_fantasia) : null; if (fantasia) box.append(fantasia);
  box.append(linha("CNPJ", cnpjFmt(d.cnpj) ?? null),
    linha("Endereço", [[d.rua, d.numero].filter(Boolean).join(", "), d.complemento, d.bairro, [d.cidade, d.estado].filter(Boolean).join(" - ")].filter(Boolean).join(" · ")),
    linha("Situação", d.situacao_nome), linha("Porte", d.porte_nome), linha("Categoria", d.categorias.map((c) => c.nome).join(", ")));
  const a = document.createElement("a"); a.href = `/empresas/${d.id}`; a.textContent = "Ver empresa"; a.className = "btn btn-primary";
  a.onclick = (e) => { e.preventDefault(); onVer(d.id); }; box.append(a);
  return box;
}

export default function MapaView({ marcadores, selecionado, ajustar, onVer, onMover }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const mapa = useRef<L.Map | null>(null);
  const grupo = useRef<L.MarkerClusterGroup | null>(null);
  const porId = useRef(new Map<number, L.CircleMarker>());
  const cb = useRef({ onVer, onMover }); cb.current = { onVer, onMover };
  const ultimoAjuste = useRef(-1);
  const detalhes = useRef(new Map<number, Detalhe>());

  useEffect(() => {
    if (!el.current) return;
    const m = L.map(el.current, { center: [-7.94, -34.87], zoom: 11 });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© Colaboradores do OpenStreetMap" }).addTo(m);
    grupo.current = L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 50 }); m.addLayer(grupo.current);
    m.on("moveend", () => { const b = m.getBounds(); cb.current.onMover?.({ sul: b.getSouth(), oeste: b.getWest(), norte: b.getNorth(), leste: b.getEast() }); });
    mapa.current = m;
    return () => { m.remove(); mapa.current = null; grupo.current = null; porId.current.clear(); };
  }, []);

  useEffect(() => {
    const g = grupo.current, m = mapa.current; if (!g || !m) return;
    g.clearLayers(); porId.current.clear();
    const layers = marcadores.map((x) => {
      const c = L.circleMarker([x.lat, x.lon], { radius: x.aprox ? 6 : 8, fillColor: x.cor, fillOpacity: x.aprox ? 0.75 : 0.9, color: x.revisao ? "#f59e0b" : "#ffffff", weight: x.revisao ? 3 : 2,
        dashArray: x.desatualizada ? "3 3" : undefined });
      c.bindPopup("Carregando…", { maxWidth: 320 });
      c.on("popupopen", async () => {
        try {
          let d = detalhes.current.get(x.id);
          if (!d) { d = await api<Detalhe>(`/api/mapa/empresas/${x.id}`); detalhes.current.set(x.id, d); }
          c.setPopupContent(montarPopup(d, cb.current.onVer));
        } catch { c.setPopupContent("Não foi possível carregar os dados."); }
      });
      porId.current.set(x.id, c); return c;
    });
    g.addLayers(layers);
    if (ajustar !== ultimoAjuste.current && layers.length > 0) { m.fitBounds(g.getBounds().pad(0.1), { maxZoom: 17 }); }
    ultimoAjuste.current = ajustar;
  }, [marcadores, ajustar]);

  useEffect(() => {
    if (!selecionado) return;
    const c = porId.current.get(selecionado.id), g = grupo.current; if (!c || !g) return;
    g.zoomToShowLayer(c, () => c.openPopup());
  }, [selecionado]);

  return <div ref={el} className="mapa" role="region" aria-label="Mapa de empresas" />;
}
