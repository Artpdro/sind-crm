#!/usr/bin/env bash
# Instala o sistema num Ubuntu 22.04/24.04 limpo. Rode como root (sudo), de dentro da pasta deploy/.
# Pré-requisito: o projeto já está em /opt/sindnorte (backend/ e frontend/).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Rode com sudo"; exit 1; }
[ -d /opt/sindnorte/backend ] || { echo "Coloque o projeto em /opt/sindnorte"; exit 1; }
DEPLOY="$(cd "$(dirname "$0")" && pwd)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip unzip curl ufw fail2ban unattended-upgrades \
  debian-keyring debian-archive-keyring apt-transport-https gpg sqlite3 awscli

# Swap de 2 GB (servidores de 1-2 GB de RAM: protege a importação do Excel e a geração de PDF)
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'vm.swappiness=10' > /etc/sysctl.d/99-swap.conf && sysctl -p /etc/sysctl.d/99-swap.conf
fi

# Node 20 só é necessário se o frontend NÃO tiver sido compilado no seu computador
if [ ! -f /opt/sindnorte/frontend/dist/index.html ]; then
  if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
  fi
fi

# Caddy
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi

# Usuário sem privilégios
id sindnorte >/dev/null 2>&1 || useradd --system --home /opt/sindnorte --shell /usr/sbin/nologin sindnorte
mkdir -p /opt/sindnorte/backend/data /var/backups/sindnorte /var/log/caddy
chown -R sindnorte:sindnorte /opt/sindnorte
chown caddy:caddy /var/log/caddy || true

# Backend
sudo -u sindnorte bash -c 'cd /opt/sindnorte/backend && python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt'
if [ ! -f /opt/sindnorte/backend/.env ]; then
  cp "$DEPLOY/env.production.example" /opt/sindnorte/backend/.env
  echo ">>> EDITE /opt/sindnorte/backend/.env (troque SEUDOMINIO) e rode este script de novo."
  chown sindnorte:sindnorte /opt/sindnorte/backend/.env; chmod 600 /opt/sindnorte/backend/.env
  exit 0
fi
chmod 600 /opt/sindnorte/backend/.env
sudo -u sindnorte bash -c 'cd /opt/sindnorte/backend && .venv/bin/python -m scripts.init_db'

# Frontend: usa a pasta dist enviada do seu computador (recomendado). Sem ela, compila aqui (lento, usa swap).
if [ ! -f /opt/sindnorte/frontend/dist/index.html ]; then
  echo ">>> frontend/dist não encontrado: compilando no servidor (pode levar alguns minutos)"
  sudo -u sindnorte bash -c 'cd /opt/sindnorte/frontend && npm ci && NODE_OPTIONS=--max-old-space-size=1024 npm run build'
fi
chown -R sindnorte:sindnorte /opt/sindnorte/frontend/dist
chmod -R a+rX /opt/sindnorte/frontend/dist

# Serviço
cp "$DEPLOY/sindnorte.service" /etc/systemd/system/sindnorte.service
systemctl daemon-reload
systemctl enable --now sindnorte

# Caddy
if [ ! -f /etc/caddy/Caddyfile.sindnorte-ok ]; then
  cp "$DEPLOY/Caddyfile" /etc/caddy/Caddyfile
  echo ">>> EDITE /etc/caddy/Caddyfile (troque SEUDOMINIO), depois: touch /etc/caddy/Caddyfile.sindnorte-ok && systemctl reload caddy"
fi

# Firewall do sistema (além do firewall do provedor)
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# fail2ban (SSH) e atualizações de segurança automáticas
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades

# Backup diário às 03:10
install -m 755 "$DEPLOY/backup.sh" /usr/local/bin/sindnorte-backup
echo '10 3 * * * sindnorte /usr/local/bin/sindnorte-backup >> /var/log/sindnorte-backup.log 2>&1' > /etc/cron.d/sindnorte-backup
touch /var/log/sindnorte-backup.log && chown sindnorte:sindnorte /var/log/sindnorte-backup.log

echo "Pronto. Crie o administrador:  sudo -u sindnorte bash -c 'cd /opt/sindnorte/backend && .venv/bin/python -m scripts.admin create-admin SEU_USUARIO'"
