#!/usr/bin/env bash
# Atualiza o sistema depois de enviar arquivos novos para /opt/sindnorte. Rode com sudo.
set -euo pipefail
sudo -u sindnorte /usr/local/bin/sindnorte-backup || true
chown -R sindnorte:sindnorte /opt/sindnorte
sudo -u sindnorte bash -c 'cd /opt/sindnorte/backend && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m scripts.init_db'
# O frontend deve ser compilado no seu computador (deploy/publicar-front.sh) e enviado para /opt/sindnorte/frontend/dist.
# Só compila aqui se a pasta dist não existir.
if [ ! -f /opt/sindnorte/frontend/dist/index.html ]; then
  sudo -u sindnorte bash -c 'cd /opt/sindnorte/frontend && npm ci && NODE_OPTIONS=--max-old-space-size=1024 npm run build'
fi
chown -R sindnorte:sindnorte /opt/sindnorte/frontend/dist
chmod -R a+rX /opt/sindnorte/frontend/dist
systemctl restart sindnorte
systemctl status sindnorte --no-pager | head -5
