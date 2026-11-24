# Segurança — modelo de ameaças, controles e operação

Sistema: gestão e mapa de empresas do Sindnorte (FastAPI + SQLite + React). Este documento diz **o que está protegido, por qual mecanismo, qual teste prova, e o que ficou de fora**. Última revisão: Fase 6 (outubro/2026).

## 1. O que protegemos
| Ativo | Por quê importa |
|---|---|
| Cadastro de empresas (CNPJ, e-mails, telefones, endereços, observações) | dados pessoais possíveis (MEI/empresário individual) → LGPD |
| Histórico de interações e auditoria | prova de atendimento; deve ser imutável |
| Contas e sessões (senhas, tokens) | porta de entrada para tudo |
| Arquivo do banco, backups, PDFs gerados | cópia completa dos dados fora do sistema |
| Disponibilidade da importação/geocodificação | uso interno; falha não pode corromper dados |

## 2. Quem atacaria (e como) — modelo de ameaças (STRIDE resumido)
| Ameaça | Cenário | Controle no código | Prova (teste) |
|---|---|---|---|
| **S**poofing | adivinhar/forçar senha; roubar sessão | Argon2id; bloqueio 5 falhas/15 min; resposta igual p/ usuário inexistente (anti-enumeração); cookie HttpOnly `__Host-` + SameSite; token só como hash SHA-256; expira em 30 min ocioso / 8 h; rotação | `test_auth.py` |
| **T**ampering | CSRF; mass assignment; editar histórico/auditoria; dois operadores sobrescrevendo | token CSRF + checagem de Origin; modelos `extra="forbid"`; triggers que bloqueiam UPDATE/DELETE em histórico e auditoria; bloqueio otimista (`version`) | `test_auth`, `test_db_integrity`, `test_concorrencia_e_regressao` |
| **R**epudiation | "não fui eu que alterei" | auditoria imutável (quem, quando, IP, ação; guarda *quais campos* mudaram, não os valores) | `test_db_integrity` |
| **I**nformation disclosure | SQL injection; XSS; vazamento em erro; cache de dados pessoais; PDF/CSV com fórmula | consultas 100% parametrizadas (filtros montados por fragmentos fixos); React escapa; popup do mapa usa `textContent`; erros 500 genéricos; validação 422 sem refletir o valor; **`Cache-Control: no-store` em toda `/api`**; texto no PDF é escapado; células de planilha com `=`/`+`/`-`/`@` são neutralizadas | `test_concorrencia_e_regressao` (payloads SQL/HTML), `test_importacao`, `test_relatorios` |
| **D**enial of service | planilha-bomba; upload gigante; geração de PDF em loop; martelar login | limite 5 MB / 20 mil linhas; validação de zip (razão de compressão, macros, entidades XML); corpo limitado antes de ler; 30 PDFs/h por usuário; limites em login/importação/geocodificação; mapa limita marcadores; PDF limita empresas e atividades | `test_importacao`, `test_relatorios`, `test_mapa` |
| **E**levation of privilege | operador/visualizador chamando ação de admin; trocar `id` na URL | RBAC no servidor em cada rota (admin/operador/visualizador); autor sempre = usuário da sessão; empresa excluída some de lista, mapa, PDF e painel | `test_rbac_users`, `test_concorrencia_e_regressao` |
| **SSRF** | fazer o servidor acessar rede interna via geocodificador/mapa | destino **fixo** vindo do `.env`; só https; bloqueia localhost, IP privado, link-local, metadados (169.254.169.254); **sem seguir redirecionamentos**; resposta limitada; blocos de mapa só PNG 256×256 ≤ 300 KB; cache sem sair da pasta | `test_geocoding`, `test_pdf_mapa_ruas` |
| Upload malicioso | .xlsx com macro, link externo, caminho `../` | só `.xlsx`; processado em memória (nunca no disco); rejeita macro, externalLinks, criptografia, DOCTYPE/ENTITY | `test_importacao` |
| Corrida / integridade | duas confirmações da mesma importação; dois geocodificadores; mesmo CNPJ cadastrado juntos | `BEGIN IMMEDIATE`; vencedor único na confirmação; trava (`geocode_lease`); `UNIQUE` de CNPJ **no banco**; `version` | `test_importacao`, `test_empresas`, `test_concorrencia_e_regressao` |

## 3. Mapa de confiança (onde os dados cruzam fronteiras)
- **Navegador ⇄ API**: cookie de sessão + CSRF. Tudo é validado de novo no servidor.
- **Servidor → OpenStreetMap (Nominatim e blocos de mapa)**: sai **endereço de empresa** (consulta) e **coordenadas** (blocos). Não sai CNPJ, nome, e-mail nem telefone. Com o CNEFE importado, a maioria das empresas **nem consulta** a internet.
- **IBGE (CNEFE)**: só entrada de dados, importada pelo administrador via terminal (`scripts.cnefe`), nunca por upload no site.
- **Disco**: `data/sindnorte.db` (todos os dados), `data/cnefe.db` (público), `data/tiles` (público), `backups/` (dados pessoais).

## 4. LGPD (o que o sistema faz e o que cabe à organização)
- Minimização: auditoria não guarda valores antigos/novos de dados pessoais; popup do mapa não mostra e-mail/telefone; o diagnóstico de localização é restrito a administrador e operador (visualizador não vê).
- Exclusão lógica + histórico imutável: se for preciso apagar de fato um titular, é uma operação administrativa fora do uso normal (anonimizar os campos; o histórico mantém o vínculo por id). **Defina o procedimento com o jurídico.**
- **Cabe à organização**: base legal e finalidade, aviso aos titulares, prazo de retenção, quem pode ter conta, guarda dos backups e dos PDFs (cada PDF traz o aviso "uso interno").

## 5. Riscos que continuam (decisões conscientes)
| Risco | Situação | O que fazer |
|---|---|---|
| Sem HTTPS na máquina local | `http://localhost` é aceitável só no próprio computador | em rede/servidor: proxy HTTPS (Caddy/Nginx), `COOKIE_SECURE=true`, `APP_ENV=production` (o sistema recusa subir sem isso) |
| Sem autenticação em dois fatores | senha forte + bloqueio, mas sem 2FA | recomendado antes de expor na internet |
| Backups e banco sem criptografia própria | o SQLite é um arquivo comum | criptografia de disco (BitLocker/LUKS); backup em local controlado |
| Um único servidor / SQLite | bom para uso interno; sem alta disponibilidade | `docs/postgres-producao/` tem o esquema de referência (PostgreSQL + PostGIS) para crescer |
| Geração de PDF pode segurar um processo por até ~25 s (download de blocos) | limitada por usuário (30/h) e cache de blocos | em muitos usuários simultâneos, aumente workers ou desligue `PDF_MAPA_RUAS` |
| Precisão da localização depende do CNEFE/OSM | endereços novos ou mal escritos caem em "estimada"/"revisão" | revisão humana + correção manual (já prevista) |
| Sem tela para gerir usuários e campos personalizados | só pela API/terminal | `scripts.admin` cobre criar admin e redefinir senha |
| **DAST não executado** | o ambiente onde o código foi escrito não tinha o alvo rodando para um scanner | rode o roteiro da seção 7 antes de expor o sistema |

## 6. Verificações automáticas (CI: `.github/workflows/ci.yml`)
`pytest` (527 testes na última execução) · `bandit` (SAST) · `pip-audit` e `npm audit` (dependências; ambos sem vulnerabilidades conhecidas em 21/10/2026) · `gitleaks` (segredos no histórico) · build do frontend · job manual **ZAP baseline** (DAST) descrito abaixo.

## 7. Roteiro de DAST (OWASP ZAP baseline) — fazer antes de publicar
1. Suba o sistema num ambiente de teste (nunca contra produção) com dados fictícios.
2. `docker run --rm -t --network host -v "$PWD:/zap/wrk" ghcr.io/zaproxy/zaproxy:stable zap-baseline.py -t http://localhost:5173 -r zap.html` (ou use o job **dast** do GitHub Actions, que sobe o sistema e roda o baseline).
3. Faça uma segunda passada **autenticada** (ZAP → Contexts/Users) para alcançar as telas e rotas que exigem login.
4. Para cada alerta: corrigir **+ teste de regressão + registrar aqui**. Alertas esperados e aceitáveis: ausência de `Strict-Transport-Security` em `http://localhost` (só vale com HTTPS); cabeçalhos do servidor de desenvolvimento do Vite (o build de produção deve ser servido por Nginx/Caddy com CSP própria — ver seção 8).

## 8. Checklist para colocar em produção
- [ ] `APP_ENV=production`, `COOKIE_SECURE=true`, `ALLOWED_ORIGINS` só com o domínio real, `TRUST_PROXY_HEADERS=true` **apenas** atrás de proxy confiável
- [ ] HTTPS válido; frontend servido do build (`npm run build`) com CSP: `default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'`
- [ ] `.env` fora do git, permissão só do dono; senhas de admin fortes e únicas; SMTP configurado se quiser "esqueci minha senha"
- [ ] `GEOCODER_EMAIL` real (política do OSM)
- [ ] Backup agendado: `python -m scripts.backup --manter 14` (Agendador de Tarefas do Windows ou cron), cópia fora da máquina, **teste de restauração** feito uma vez
- [ ] Disco criptografado; atualizações do sistema operacional em dia
- [ ] DAST (seção 7) sem alertas médios/altos abertos
- [ ] Rotina de atualização de dependências (`pip-audit`, `npm audit`) a cada release

## 9. Registro de achados desta revisão
| # | Achado | Gravidade | Correção | Teste |
|---|---|---|---|---|
| 1 | Respostas de `/api/dashboard` e `/api/relatorios/*` saíam **sem `Cache-Control: no-store`** (a lista de rotas protegidas era manual e esqueceu as novas) | média | regra invertida: **toda** `/api/*` recebe `no-store` (rota nova já nasce protegida) | `test_cabecalhos_de_seguranca_nos_novos_endpoints` |
| 2 | `vite` 8.3.x com 3 avisos "high" (`launch-editor`, só no servidor de desenvolvimento no Windows) | alta (dev) | `npm audit fix` → 0 vulnerabilidades | CI `npm audit` |
| 3 | Sem rotina de backup | média | `scripts/backup.py` (cópia consistente com sistema ligado, verificação de integridade, retenção) | `test_backup_*` |
| 4 | Concorrência de edição, trava do geocodificador, interações e PDFs simultâneos sem prova automatizada | baixa | testes com threads | `test_concorrencia_e_regressao` |
