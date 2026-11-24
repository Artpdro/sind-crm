# Sindnorte — Gestão de Empresas (versão local, Fases 1 a 4)

Estado: **Fases 1 e 2**
- Fase 1: login com usuário e senha, perfis (Administrador / Operador / Visualizador), auditoria, banco **SQLite local** (`backend/data/sindnorte.db`, sem servidor de banco).
- Fase 2: empresas (cadastrar, editar, excluir, listar com busca/filtros/paginação), **consulta por CNPJ**, perfil completo, **histórico de interações**,
  situações/categorias/portes/tipos administráveis e **campos personalizados**.
- Fase 3: **importação do Excel (.xlsx)** com validação, prévia, confirmação, modos (cadastrar novos / atualizar existentes / ambos) e empresas marcadas para revisão.
- Fase 4: **geocodificação automática** (toda empresa nova ou com endereço alterado é mapeada sozinha) e **Mapa de Empresas** (pesquisa por rua/bairro/número/cidade, pares/ímpares, filtros, lista, agrupamento de marcadores).
- Próximas: PDF e dashboard (Fase 5), revisão final de segurança (Fase 6).

**Acesse por `http://localhost:5173`.** Perfis: Visualizador só consulta; Operador cadastra, edita e registra interações; Administrador também exclui empresas e gerencia cadastros e usuários.

## Rodar localmente (precisa só de Python 3.12+ e Node 20+)

**Backend** (terminal 1):
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate        Linux/Mac: source .venv/bin/activate
pip install -r requirements-dev.txt          # (se atualizou de uma versão anterior, rode de novo)
cp .env.example .env                     # Windows: copy .env.example .env
python -m scripts.init_db                # cria/atualiza o banco (a API também faz isso ao iniciar; nunca apaga dados)
python -m scripts.admin create-admin SEU_USUARIO     # pede a senha (mín. 10 caracteres, letra + número/símbolo)
uvicorn app.main:app --port 8000
```
**Frontend** (terminal 2):
```bash
cd frontend
npm install
npm run dev                              # abra http://localhost:5173
```

## Testes e verificações
```bash
cd backend && pytest                     # 451 testes; cada um usa um banco SQLite temporário
bandit -q -r app scripts && pip-audit -r requirements.txt
cd ../frontend && npm audit
```

## Regras de dados já implementadas
- **ID interno** da empresa gerado pelo banco; **CNPJ normalizado (14 dígitos, texto) com UNIQUE no banco**. Empresas sem CNPJ são permitidas (a planilha tem 21); CNPJ com formato ou dígito verificador inválido é recusado.
- CNPJ, CEP e telefones são sempre **texto** (zeros à esquerda preservados). O **número do endereço é texto livre** (10A, S/N, Loja 01...); a parte numérica é guardada à parte para o futuro filtro par/ímpar.
- Empresa excluída some das telas (**exclusão lógica**); o histórico de interações nunca é apagado nem editado (nem pelo banco).
- Mudar o endereço marca a localização como desatualizada **sem apagar as coordenadas anteriores**.
- Edição com **bloqueio otimista**: se outra pessoa alterou a empresa, o salvamento é recusado e você recarrega.
- A auditoria registra *quais campos* mudaram, não os valores (minimização de dados, LGPD).
- **Duplicatas na importação (Fase 3):** quando o mesmo CNPJ aparecer em mais de uma linha, será mantido o registro mais completo.

## Importação do Excel (só Administrador) — menu "Importar"
1. **Enviar** o .xlsx: o sistema valida e mostra a **prévia** (nada é gravado ainda): novas, para atualizar, sem alteração, duplicadas, rejeitadas e quais entrarão para revisão.
2. **Escolher o modo** e **confirmar**. É tudo-ou-nada: se algo falhar, nenhuma empresa é alterada. Cada importação só pode ser confirmada uma vez.
- **Identificação por CNPJ** (normalizado). Mesmo CNPJ em mais de uma linha: **fica a linha mais completa** (empate: a primeira).
- **Linhas sem CNPJ ou com CNPJ inválido/incompleto**: entram como **empresas novas marcadas "Revisar"**; o valor original do CNPJ fica guardado (`cnpj_original`) e nada é inventado. O mesmo vale para CEP incompleto, telefone/email inválidos (o valor original vai para o motivo) e endereço sem rua ou sem cidade. Reimportar o mesmo arquivo não duplica essas linhas (comparação por nome + rua + número + cidade).
- **Atualizar** só troca valores que vieram preenchidos e diferentes; célula vazia **nunca apaga** dado existente; situação, porte, categorias, campos personalizados, informações adicionais e coordenadas não são tocados. Email antigo substituído vai para os emails adicionais. Mudou o endereço: a localização é marcada como desatualizada (coordenadas preservadas).
- "Cidade - Estado" (ex.: "Olinda - Pernambuco") é separado em cidade e UF. Textos que começam com `= + - @` ganham um apóstrofo (**neutralização de fórmulas**, para futuras exportações).
- **Segurança do upload**: só `.xlsx` (para `.xls`, salve como .xlsx no Excel); limite de 5 MB e 20.000 linhas; o arquivo **não é gravado em disco**, é lido em memória; recusa macros, objetos incorporados, vínculos externos, ZIP com caminhos perigosos, zip bomb, planilha com senha e qualquer declaração XML (`DOCTYPE`/`ENTITY`); fórmulas nunca são calculadas; até 10 envios por hora.
- **LGPD**: as linhas da planilha ficam guardadas só enquanto a prévia está aberta (apagadas ao confirmar, cancelar ou depois de 24 h). A auditoria registra a operação e as contagens, nunca dados das empresas.

## Mapa e geocodificação (Fase 4)
**Regra de ouro: o mapa só lê coordenadas já gravadas no banco. Abrir o mapa nunca consulta serviço externo** (testado).
- **Automática**: ao iniciar a API, uma tarefa em segundo plano mapeia as empresas na fila (`pendente`) a 1 consulta/s e dorme quando não há nada. Cadastro, edição de endereço, importação e "pedir nova geocodificação" a acordam na hora. Se o servidor for reiniciado, retoma de onde parou. Desligue com `GEOCODER_AUTO=false`.
- Para as ~3.900 empresas importadas: de ~30 min a ~2 h com o servidor ligado (depende de quantas buscas o OpenStreetMap responde; o sistema aprende sozinho a pular a busca "com número" quando ela quase nunca acha) (ou use `python -m scripts.geocode`, que pode pausar/continuar; só um geocodificador roda por vez). **Teste a qualidade antes**: `python -m scripts.geocode --amostra 15 --dry-run` mostra o que seria encontrado sem gravar nada.
- **Quem recebe os endereços**: o serviço público Nominatim (OpenStreetMap) — envie o seu contato em `GEOCODER_EMAIL`. Política de uso: máx. 1 consulta/s, User-Agent identificado, sem uso abusivo; sem garantia de disponibilidade. Alternativas: Nominatim próprio (`GEOCODER_URL` + `GEOCODER_ALLOW_LOCAL=true`) ou serviço pago (Google/Mapbox, exigiria trocar o cliente). Mapa de fundo: tiles do OpenStreetMap (uso interno leve é permitido; para muito acesso, use outro provedor).
- Estados: **Localizado** (achado na cidade certa, com o número **ou pela rua** — o OSM quase nunca tem o número no Brasil; pontos "aproximados" aparecem menores no mapa) · **Para revisão** (só pelo CEP/bairro, em rodovia/estrada, ou em cidade diferente da cadastrada) · **Não encontrado** · **Pendente**.
- **Localização EXATA por número (recomendado)**: o OpenStreetMap quase não tem número de casa no Brasil; o **CNEFE do IBGE (Censo 2022)** tem rua, número e coordenada. Baixe o `.zip` de cada município no site do IBGE (Geociências → Downloads → *Cadastro Nacional de Endereços para Fins Estatísticos* → Censo Demográfico 2022 → Pernambuco → arquivo do município, ex.: Paulista 2610707, Olinda 2609600, Igarassu 2606804, Abreu e Lima 2600054, Ilha de Itamaracá 2607604, Itapissuma 2607752), e rode na pasta `backend`: `python -m scripts.cnefe importar caminho\2610707_PAULISTA.zip` (pode passar vários; se o município não for reconhecido use `--nome "Cidade"`), depois `python -m scripts.cnefe aplicar` para refazer as localizações (manuais são mantidas). A base fica em `data/cnefe.db` (não vai para o git) e funciona **sem internet**. Ordem de busca: CNEFE número exato (*endereço exato*) → número vizinho do mesmo lado da rua (*estimada entre vizinhos*, marcada como aproximada, com os números usados na observação) → ponto médio da rua → só se a rua não existir no CNEFE, OpenStreetMap. Limites honestos: o CNEFE é cadastro de endereços do censo (nem toda empresa nova tem ponto); nomes muito diferentes entre planilha e IBGE caem no OpenStreetMap; confira o resultado com `python -m scripts.geocode --diagnostico` (mostra quantas ficaram por fonte e precisão) e corrija manualmente os casos duvidosos. `python -m scripts.cnefe status` lista o que foi importado.
- **Se o mapa não estiver mapeando**: menu **Localização** → painel de **Diagnóstico** (motivos agrupados, último erro do serviço, exemplos), botão **Testar conexão** e **Devolver à fila** (refaz as não encontradas / para revisão com a busca atual). No terminal: `python -m scripts.geocode --testar` (1 consulta: mostra se o serviço responde ou bloqueia), `--diagnostico` (sem internet) e `--amostra 15 --dry-run --detalhe` (mostra cada busca tentada). Os campos de endereço são limpos antes da busca (lixo depois da vírgula, "1ª Travessa", "Rodovia BR-101 Norte", "Ilha de Itamaracá"→"Itamaracá"); quando o campo "rua" traz só o nome do município (≈ 5% da planilha), isso **não é buscado como rua**: usa CEP/bairro ou explica o motivo. A empresa continua cadastrada em qualquer caso. Se o endereço mudar, o marcador antigo **fica no mapa** até uma nova localização válida substituí-lo. Correção manual (latitude/longitude) no perfil.
- Pesquisa: **rua** = "contém" (sem acentos/maiúsculas); **bairro** e **cidade** = igualdade exata; **número** "100" também acha 100A, 100-B e 100/102; pares/ímpares usam o número-base, e **S/N, Fundos etc. ficam de fora** (o mapa informa quantas). Lista, contador e mapa usam os mesmos filtros.
- Segurança: o endereço de empresas só vai como parâmetro codificado da consulta; destino fixo e validado (bloqueia localhost/redes internas/metadados, não segue redirecionamentos); popup do mapa montado sem HTML (anti-XSS).

## Segurança e operação (Fase 6)
Leia **`docs/SEGURANCA.md`**: modelo de ameaças, mapa de controles com os testes que os provam, riscos que continuam, roteiro de DAST e checklist de produção. Backup: `python -m scripts.backup --manter 14` (funciona com o sistema ligado; restaurar = parar o servidor e copiar o arquivo por cima de `data/sindnorte.db`).

## Relatórios em PDF e painel (Fase 5)
- **Mapa → Baixar PDF**: gera o PDF com **exatamente** as empresas da busca atual (mesmos filtros da lista e do mapa, inclusive rua, número, par/ímpar, porte, tipo, categoria, situação e campos personalizados). Traz critérios, totais, mapa com as **ruas do OpenStreetMap** (imagem montada no servidor a partir dos blocos de mapa, com os pontos das empresas por cima, escala e crédito do OSM; os pontos são numerados como na lista quando há até 60) — sem internet ou se o serviço de mapas falhar, o PDF usa um mapa esquemático e avisa, dados de cada empresa, classificação da localização e a **última interação**. Com filtro de rua/número a lista sai ordenada por endereço (serve como relatório por rua ou número). Limite: 1500 empresas por PDF (`MAX_PDF_EMPRESAS`); acima disso o sistema pede para refinar a busca. Os blocos de mapa ficam em cache em `backend/data/tiles` por 14 dias (a política do OpenStreetMap pede cache e uso moderado: cada PDF usa no máximo ~30 blocos, e áreas muito grandes caem no mapa esquemático). Segurança do download: endereço fixo vindo do `.env` (`TILES_URL`, só https, bloqueia localhost/rede interna e redirecionamentos), só aceita PNG 256×256 de até 300 KB. Para desligar a imagem de ruas: `PDF_MAPA_RUAS=false`.
- **Relatórios → Atividades por data**: escolha período, tipo de atividade, responsável, situação e cidade; veja o resumo (por tipo, responsável e dia) e a lista na tela, e baixe o PDF. Baseia-se nas interações registradas nas empresas. As datas valem pelo **horário de Recife** (UTC-3). Período máximo de 366 dias; até 3000 atividades por PDF (`MAX_PDF_ATIVIDADES`).
- **Início**: painel com totais, empresas no mapa, cadastros para revisão, atividades dos últimos 30 dias, distribuição por situação/cidade/porte/tipo/categoria, cadastros e atividades recentes.
- Segurança: login obrigatório; filtros validados e parametrizados; todo texto do banco é escapado antes de entrar no PDF (marcação não vira formatação); limite de 30 PDFs por hora por usuário; cada geração é auditada (quais filtros e quantas empresas, sem gravar os dados). O PDF contém dados pessoais: o rodapé avisa uso interno.

## Arquivo `.env`: comentários
Não escreva comentário **na mesma linha** de um valor vazio (`CHAVE=   # texto`): o leitor de `.env` entende o comentário como valor. O `.env.example` já vem com os comentários em linhas separadas e o sistema ignora valores que começam com `#`. `GEOCODER_EMAIL` precisa ser um e-mail válido ou ficar vazio.

## Backup
Com a API parada, copie `backend/data/sindnorte.db` (e, se existirem, os arquivos `-wal` e `-shm`).

## Limitações desta versão local
- **SQLite não tem PostGIS.** Latitude/longitude ficam em colunas numéricas com índice; a busca por área usa "caixa" (latitude/longitude mín. e máx.). "Empresas próximas" exata, em escala, volta com PostgreSQL na produção.
- **Sem usuários de banco com privilégios diferentes.** O histórico de interações e a auditoria são protegidos por *triggers* (nenhum UPDATE/DELETE, nem pela aplicação), mas quem tem acesso ao arquivo do banco pode alterá-lo: proteja a pasta `data/` e o computador.
- **Escritas são serializadas** (uma de cada vez). Para o uso local de poucos usuários isso não é problema.
- `numero_base` (parte numérica do número do endereço, usada no filtro par/ímpar) é calculada pela aplicação, não pelo banco.
- Não serve para hospedar na Vercel (o disco de lá é temporário). Para publicar, será preciso voltar ao PostgreSQL (ver `docs/postgres-producao/`).
- **Ainda sem tela:** gerenciar campos personalizados e usuários (existem na API: `/api/campos`, `/api/users`; documentação interativa em http://localhost:8000/api/docs com a API rodando).
- PDF e dashboard ainda não existem (botão "Baixar PDF" do mapa está desativado).
- **A qualidade real da geocodificação ainda não foi medida** (o ambiente de desenvolvimento não alcança o OpenStreetMap; os testes usam um serviço simulado). Rode a amostra com `--dry-run` e confira.
- Planilhas `.xls` (formato antigo) não são lidas por segurança; salve como `.xlsx`. Empresa **excluída** que reaparece na planilha pelo mesmo CNPJ é rejeitada (ainda não há tela para restaurar).
- Reset de senha por e-mail exige SMTP; sem ele, use `python -m scripts.admin reset-password USUARIO`.
