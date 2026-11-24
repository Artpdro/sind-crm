#!/usr/bin/env bash
# Rode no SEU computador (Linux/macOS), na raiz do projeto: compila o frontend e envia só a pasta dist.
# Uso:  bash deploy/publicar-front.sh ubuntu@IP_DO_SERVIDOR  [caminho/da/chave.pem]
set -euo pipefail
ALVO="${1:?uso: publicar-front.sh usuario@servidor [chave.pem]}"
CHAVE="${2:-}"
SSH_OPTS=(); [ -n "$CHAVE" ] && SSH_OPTS=(-i "$CHAVE")
( cd frontend && npm ci && npm run build )
tar -czf /tmp/sindnorte-dist.tgz -C frontend dist
scp "${SSH_OPTS[@]}" /tmp/sindnorte-dist.tgz "$ALVO:/tmp/"
ssh "${SSH_OPTS[@]}" "$ALVO" 'sudo rm -rf /opt/sindnorte/frontend/dist && sudo mkdir -p /opt/sindnorte/frontend && sudo tar -xzf /tmp/sindnorte-dist.tgz -C /opt/sindnorte/frontend && sudo chown -R sindnorte:sindnorte /opt/sindnorte/frontend/dist && sudo chmod -R a+rX /opt/sindnorte/frontend/dist && rm /tmp/sindnorte-dist.tgz'
echo "Frontend publicado."
