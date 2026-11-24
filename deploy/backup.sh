#!/usr/bin/env bash
# Backup diário: cópia consistente do SQLite + envio para fora do servidor (S3), se configurado.
# Para o S3: crie /etc/sindnorte-s3.env (chmod 640, dono root:sindnorte) com:
#   S3_BUCKET=s3://nome-do-bucket/sindnorte
#   AWS_ACCESS_KEY_ID=...   AWS_SECRET_ACCESS_KEY=...   AWS_DEFAULT_REGION=sa-east-1
# Use um usuário IAM que só possa gravar nesse bucket. Ative criptografia e versionamento no bucket.
set -euo pipefail
cd /opt/sindnorte/backend
.venv/bin/python -m scripts.backup --destino /var/backups/sindnorte --manter 14
ULTIMO="$(ls -1t /var/backups/sindnorte/sindnorte-*.db | head -1)"
if [ -r /etc/sindnorte-s3.env ]; then
  set -a; . /etc/sindnorte-s3.env; set +a
  gzip -k -f "$ULTIMO"
  aws s3 cp "$ULTIMO.gz" "$S3_BUCKET/" --sse AES256
  rm -f "$ULTIMO.gz"
else
  echo "AVISO: sem /etc/sindnorte-s3.env; backup só neste servidor." >&2
fi
