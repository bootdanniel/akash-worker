#!/data/data/com.termux/files/usr/bin/bash

KEY=$(cat ~/.akash_key)
SDL="$HOME/akash-worker/deploy.yaml"

echo "=========================================="
echo "  DEPLOY AKASH — Supernatural Worker"
echo "=========================================="
echo ""
echo "SDL: $SDL"
echo ""

# 1. Enviar
echo ">>> [1/4] Enviando SDL pro Akash..."
RESP=$(curl -s -X POST \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"data\":{\"sdl\":$(python3 -c "import json; print(json.dumps(open('$SDL').read()))"),\"deposit\":0.5}}" \
  "https://console-api.akash.network/v1/deployments")

echo "$RESP" | python3 -m json.tool 2>/dev/null | head -20

DSEQ=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('data',{}).get('dseq',''))" 2>/dev/null)

if [ -z "$DSEQ" ]; then
  echo "❌ Não recebeu DSEQ. Erro acima."
  exit 1
fi

echo ""
echo "✅ DSEQ: $DSEQ"
echo "$DSEQ" > ~/akash-worker/.dseq_atual

# 2. Esperar bids
echo ""
echo ">>> [2/4] Esperando bids (~30s)..."
sleep 30

echo ""
echo ">>> [3/4] Listando bids..."
BIDS=$(curl -s -H "x-api-key: $KEY" \
  "https://console-api.akash.network/v1/bids?dseq=$DSEQ")

echo "$BIDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
bids = d.get('data', [])
if not bids:
    print('❌ Nenhuma bid recebida.')
    sys.exit(1)
print(f'✅ {len(bids)} bid(s) recebidas:')
for b in bids:
    bid = b.get('bid', b)
    p = bid.get('id', {}).get('provider', '?')
    preco = bid.get('price', {}).get('amount', '?')
    print(f'   Provider: {p}')
    print(f'   Preço:    {preco} uakt')
"

echo ""
echo ">>> [4/4] Aceitando a bid mais barata..."

# Pega provider e manifest
LEASE_DATA=$(curl -s -H "x-api-key: $KEY" \
  "https://console-api.akash.network/v1/bids?dseq=$DSEQ" | python3 -c "
import json, sys
d = json.load(sys.stdin)
bids = d.get('data', [])
if not bids:
    print('')
    sys.exit(0)
# Ordena por preço
bids_sorted = sorted(bids, key=lambda b: int(b.get('bid', b).get('price', {}).get('amount', '999999999')))
best = bids_sorted[0]
b = best.get('bid', best)
print(b.get('id', {}).get('provider', ''))
")

echo "Provider escolhido: $LEASE_DATA"

# Criar lease (o manifest vem da própria API — pegamos ele da resposta do POST inicial)
echo ""
echo "DSEQ salvo em: ~/akash-worker/.dseq_atual"
echo ""
echo "=========================================="
echo "  ✅ Deployment criado!"
echo "  DSEQ: $DSEQ"
echo ""
echo "  Próximo passo: aceitar lease com o comando"
echo "  (o provider precisa enviar o manifest)"
echo "=========================================="
