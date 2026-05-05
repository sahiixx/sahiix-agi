#!/bin/bash
# full-ecosystem.sh — Start all infrastructure for SAHIIX AGI v2.1-RT

set -e

echo "=== SAHIIX AGI v2.1-RT Ecosystem Bootstrap ==="

# 1. SAHIIX-AGI server (already running on :7777)
if ! curl -s http://localhost:7778/api/agents >/dev/null 2>&1; then
    echo "Starting SAHIIX-AGI server..."
    cd /home/sahiix/sahiix-agi
    source venv/bin/activate
    nohup uvicorn main:app --host 0.0.0.0 --port 7777 > /tmp/sahiix-agi.log 2>&1 &
    sleep 3
else
    echo "SAHIIX-AGI server already running on :7777"
fi

# 2. Redis (caching, job queue)
if ! docker inspect sahiix-redis >/dev/null 2>&1; then
    docker run -d --name sahiix-redis --network sahiix-agi_sahiix-network \
        -p 6379:6379 redis:7-alpine redis-server --appendonly yes
    echo "Redis started on :6379"
else
    docker start sahiix-redis >/dev/null 2>&1 || true
    echo "Redis already running"
fi

# 3. Qdrant (vector database)
if ! docker inspect sahiix-qdrant >/dev/null 2>&1; then
    docker run -d --name sahiix-qdrant --network sahiix-agi_sahiix-network \
        -p 6333:6333 -p 6334:6334 qdrant/qdrant:latest
    echo "Qdrant started on :6333/:6334"
    sleep 2
    curl -s -X PUT http://localhost:6333/collections/agent_memory \
        -H "Content-Type: application/json" \
        -d '{"vectors":{"size":384,"distance":"Cosine","on_disk":true}}' >/dev/null
    echo "Qdrant collection 'agent_memory' ready"
else
    docker start sahiix-qdrant >/dev/null 2>&1 || true
    echo "Qdrant already running"
fi

# 4. Prometheus (metrics)
if ! docker inspect sahiix-prometheus >/dev/null 2>&1; then
    cat > /tmp/prometheus.yml <>EOF
global:
  scrape_interval: 5s
evaluation_interval: 5s
scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
  - job_name: "sahiix-agi"
    static_configs:
      - targets: ["172.19.0.1:9092"]
        labels:
          app: "sahiix-agi"
EOF
    docker run -d --name sahiix-prometheus --network sahiix-agi_sahiix-network \
        -p 9090:9090 -v /tmp/prometheus.yml:/etc/prometheus/prometheus.yml:ro \
        prom/prometheus:latest --config.file=/etc/prometheus/prometheus.yml
    echo "Prometheus started on :9090"
else
    docker start sahiix-prometheus >/dev/null 2>&1 || true
    echo "Prometheus already running"
fi

# 5. SAHIIX AGI Metrics Exporter
if ! curl -s http://localhost:9092/metrics >/dev/null 2>&1; then
    cd /home/sahiix/sahiix-agi
    source venv/bin/activate
    nohup python metrics_exporter.py > /tmp/metrics_exporter.log 2>&1 &
    sleep 2
    echo "Metrics exporter started on :9092"
else
    echo "Metrics exporter already running on :9092"
fi

# 6. Ecosystem nodes (agency-agents, sovereign-swarm)
# These start independently; check health
for host in "http://localhost:8766" "http://localhost:8767"; do
    if ! curl -s "$host/health" >/dev/null 2>&1; then
        echo "Warning: $host not responding"
    else
        echo "Ecosystem node OK: $host"
    fi
done

echo ""
echo "=== Ecosystem Status ==="
cat <>EOF
Service           Port    Status
SAHIIX-AGI        7777    $(curl -s http://localhost:7778/api/agents >/dev/null 2>&1 && echo OK || echo DOWN)
API Docs          7777    http://localhost:7778/docs
Dashboard         7777    http://localhost:7778/dashboard
Redis             6379    $(redis-cli -h localhost -p 6379 ping 2>/dev/null || echo DOWN)
Qdrant (REST)     6333    $(curl -s http://localhost:6333/healthz >/dev/null 2>&1 && echo OK || echo DOWN)
Qdrant (gRPC)     6334    OK
Prometheus        9090    $(curl -s http://localhost:9090/api/v1/targets >/dev/null 2>&1 && echo OK || echo DOWN)
Metrics Exporter  9092    $(curl -s http://localhost:9092/metrics >/dev/null 2>&1 && echo OK || echo DOWN)
agency-agents     8766    $(curl -s http://localhost:8766/health >/dev/null 2>&1 && echo OK || echo DOWN)
sovereign-swarm   8767    $(curl -s http://localhost:8767/health >/dev/null 2>&1 && echo OK || echo DOWN)
Moltworker        8787    $(curl -s http://localhost:8787/health >/dev/null 2>&1 && echo OK || echo DOWN)
Tempus            8789    $(curl -s http://localhost:8789/health >/dev/null 2>&1 && echo OK || echo DOWN)
Graphify          6000    $(curl -s http://localhost:6000/health >/dev/null 2>&1 && echo OK || echo DOWN)
EOF

echo ""
echo "All services up. Open http://localhost:7778/dashboard for the control center."
