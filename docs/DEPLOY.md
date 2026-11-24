# Publicar o sistema na internet (AWS Lightsail, São Paulo)

Arquitetura: **1 servidor Ubuntu** com Caddy (HTTPS automático) → API FastAPI (só em 127.0.0.1) → banco SQLite no disco do servidor.
Cópias: snapshot diário do Lightsail + backup diário do banco enviado a um bucket S3 (fora do servidor).

## Parte A — Criar o servidor (console da AWS)
1. Crie conta em aws.amazon.com e ative **MFA** na conta raiz. Crie um usuário IAM para o dia a dia.
2. Abra **Lightsail** → região **São Paulo (sa-east-1)** → *Create instance* → Linux/Unix → **OS Only → Ubuntu 24.04 LTS**.
3. Plano: **2 vCPU / 4 GB** (ou 2 GB se quiser economizar; o sistema roda, só fica mais folgado com 4 GB).
4. Ative **Automatic snapshots** (instância → aba *Snapshots*).
5. Aba *Networking*: crie **Static IP** e anexe à instância.
6. *IPv4 Firewall*: deixe **22** (SSH) restrito ao **seu IP** (ou ao IP do escritório), **80** e **443** abertos. Remova o resto.
7. Baixe a chave SSH padrão (ou adicione a sua) e conecte: `ssh -i chave.pem ubuntu@IP_ESTATICO`.

## Parte B — Domínio
No registro.br (ou onde está o domínio) crie um registro **A**: `empresas` → IP estático. Espere propagar (minutos a horas). Teste: `ping empresas.SEUDOMINIO.com.br`.

## Plano de 2 GB: compile o frontend no seu computador
O `npm run build` usa muita memória. Com 1–2 GB de RAM, compile no **seu** computador e envie só a pasta `dist` (o script cria o swap de 2 GB sozinho).
Na raiz do projeto, no seu computador (precisa de Node 20+):
```bash
bash deploy/publicar-front.sh ubuntu@IP_ESTATICO chave.pem
```
Faça isso **depois** da Parte C (o script usa o usuário `sindnorte`, criado pelo `instalar.sh`). Na primeira instalação, rode `instalar.sh` antes; se a pasta `dist` não existir, ele compila no servidor como plano B (mais lento).

## Parte C — Enviar o projeto
No seu computador (pasta onde está o zip):
```bash
scp -i chave.pem sindnorte-empresas-fase8.zip ubuntu@IP_ESTATICO:/tmp/
```
No servidor:
```bash
sudo apt-get update && sudo apt-get install -y unzip
sudo mkdir -p /opt/sindnorte
sudo unzip /tmp/sindnorte-empresas-fase8.zip -d /tmp/proj
sudo cp -r /tmp/proj/backend /tmp/proj/frontend /tmp/proj/deploy /opt/sindnorte/
cd /opt/sindnorte/deploy && sudo bash instalar.sh
```
Na primeira execução o script cria o `.env` e **para**. Edite e rode de novo:
```bash
sudo nano /opt/sindnorte/backend/.env      # troque SEUDOMINIO (ALLOWED_ORIGINS, FRONTEND_BASE_URL, GEOCODER_EMAIL)
sudo bash instalar.sh
```
Depois ajuste o Caddy e ligue:
```bash
sudo nano /etc/caddy/Caddyfile             # troque SEUDOMINIO
sudo touch /etc/caddy/Caddyfile.sindnorte-ok
sudo systemctl reload caddy
```
Crie o administrador:
```bash
sudo -u sindnorte bash -c 'cd /opt/sindnorte/backend && .venv/bin/python -m scripts.admin create-admin SEU_USUARIO'
```
Abra `https://empresas.SEUDOMINIO.com.br` e entre.

## Parte D — Dados
1. Entre como administrador → **Importar** → envie a planilha.
2. CNEFE (localização exata), no servidor:
```bash
sudo -u sindnorte mkdir -p /opt/sindnorte/backend/data/cnefe_zips && cd /opt/sindnorte/backend/data/cnefe_zips
B=https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/26_PE
for f in 2610707_PAULISTA 2609600_OLINDA 2606804_IGARASSU 2600054_ABREU_E_LIMA 2607604_ILHA_DE_ITAMARACA 2607752_ITAPISSUMA; do sudo -u sindnorte wget -c "$B/$f.zip"; done
cd /opt/sindnorte/backend
sudo -u sindnorte .venv/bin/python -m scripts.cnefe importar data/cnefe_zips/*.zip
sudo -u sindnorte .venv/bin/python -m scripts.cnefe aplicar
sudo -u sindnorte .venv/bin/python -m scripts.geocode --diagnostico
```

## Parte E — Backup fora do servidor (S3)
1. Console AWS → **S3** → crie um bucket privado em sa-east-1, com *Block all public access*, **criptografia** e **versionamento** ligados.
2. **IAM** → crie um usuário só para backup, com permissão apenas `s3:PutObject` nesse bucket. Gere a chave de acesso.
3. No servidor:
```bash
sudo tee /etc/sindnorte-s3.env >/dev/null <<'X'
S3_BUCKET=s3://NOME-DO-BUCKET/sindnorte
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=sa-east-1
X
sudo chown root:sindnorte /etc/sindnorte-s3.env && sudo chmod 640 /etc/sindnorte-s3.env
sudo -u sindnorte /usr/local/bin/sindnorte-backup        # teste: deve aparecer o arquivo no bucket
```
4. **Teste a restauração** pelo menos uma vez: baixe um `.gz`, descompacte, pare o serviço (`systemctl stop sindnorte`), copie para `data/sindnorte.db`, ligue de novo.

## Parte F — Rotina
- **Atualizar o backend:** envie os arquivos novos para `/opt/sindnorte` e rode `sudo bash /opt/sindnorte/deploy/atualizar.sh` (faz backup antes).
- **Atualizar o frontend:** no seu computador, `bash deploy/publicar-front.sh ubuntu@IP chave.pem`.
- **Memória:** `free -h` mostra RAM e swap. Se o swap ficar sempre cheio, suba para o plano de 4 GB.
- **Logs:** `journalctl -u sindnorte -f` (API) e `/var/log/caddy/sindnorte.log` (acessos).
- **Status:** `systemctl status sindnorte caddy`.
- **Senha esquecida:** `sudo -u sindnorte bash -c 'cd /opt/sindnorte/backend && .venv/bin/python -m scripts.admin reset-password USUARIO'`.
- A cada mês: `pip-audit` e `npm audit`, e confira se o backup do dia anterior chegou ao bucket.

## Segurança — o que já vem configurado
HTTPS com HSTS e CSP, API só em localhost, serviço sem root e com sistema de arquivos somente leitura (exceto `data`), `.env` 600, firewall do Lightsail + `ufw`, `fail2ban` no SSH, atualizações automáticas de segurança, documentação da API desligada em produção, cookies `Secure`, limite de tentativas de login por IP (via `TRUST_PROXY_HEADERS`), backups criptografados no S3.

## O que continua sendo seu
- Proteja o SSH: chave, sem senha, IP restrito. Guarde a chave e o MFA da AWS.
- Dados pessoais (LGPD): mantenha usuários com o menor perfil necessário e revise acessos periodicamente.
- Considere restringir o acesso à equipe (lista de IPs no Caddy ou VPN) e ainda não há 2FA no sistema.
- O teste dinâmico de segurança (ZAP) ainda não foi executado; rode-o contra um ambiente de teste (roteiro em `docs/SEGURANCA.md`).
