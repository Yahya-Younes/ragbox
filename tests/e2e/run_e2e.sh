#!/usr/bin/env bash
# Upload a document, wait for indexing, ask a question and check the streamed, cited answer.
set -euo pipefail
URL="${RAGBOX_URL:-http://localhost:8080}"
H=(-H "X-Ragbox: 1")

for i in $(seq 1 60); do
  curl -fsS "$URL/api/status" | grep -q '"chat":{"name":"gemma3:4b","state":"ready"' && break
  sleep 1
done
curl -fsS "$URL/api/status"; echo

printf 'The warranty lasts 24 months.\n\nReturns are accepted within 30 days.\n' > /tmp/policy.txt
curl -fsS "${H[@]}" -F "files=@/tmp/policy.txt" "$URL/api/documents"; echo
for i in $(seq 1 60); do
  curl -fsS "$URL/api/documents" | grep -q '"status":"ready"' && break
  sleep 1
done
curl -fsS "$URL/api/documents" | grep -q '"status":"ready"'

CHAT=$(curl -fsS "${H[@]}" -X POST "$URL/api/chats" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
ANSWER=$(curl -fsSN "${H[@]}" -H "Content-Type: application/json" -d '{"content":"How long is the warranty?"}' "$URL/api/chats/$CHAT/messages")
echo "$ANSWER"
grep -q '"name": "policy.txt"' <<<"$ANSWER"
grep -q 'event: done' <<<"$ANSWER"
# Security: requests without the CSRF header and from a foreign Host are refused.
test "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/api/chats")" = 403
test "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.example' "$URL/api/health")" = 400
echo "E2E OK"
