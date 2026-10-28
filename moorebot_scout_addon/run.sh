#!/usr/bin/env bash
set -e

CONFIG_PATH=/data/options.json
MINIPC_URL=$(jq --raw-output '.minipc_url // "http://192.168.1.100:8085"' $CONFIG_PATH)
MINIPC_URL=${MINIPC_URL%/}

echo "[Moorebot Scout Add-on] A ligar painel Ingress do Home Assistant ao Mini PC em: ${MINIPC_URL}"

mkdir -p /run/nginx

cat <<EOF > /etc/nginx/http.d/default.conf
server {
    listen 8099;
    client_max_body_size 20M;

    location = / {
        return 302 ha;
    }

    location / {
        proxy_pass ${MINIPC_URL}/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_buffering off;
    }
}
EOF

exec nginx -g "daemon off;"
