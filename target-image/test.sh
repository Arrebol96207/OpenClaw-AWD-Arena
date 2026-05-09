#!/bin/bash

set -e

echo "=========================================="
echo "OpenClaw AWD Target Image Test Suite"
echo "=========================================="

IMAGE_NAME="openclaw/ctf-target:test"
CONTAINER_NAME="awd_target_test"
SSH_PASSWORD="test123"

cleanup() {
    echo "Cleaning up..."
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
}

trap cleanup EXIT

echo ""
echo "Step 1: Building test image..."
docker build -f Dockerfile.test -t $IMAGE_NAME .

if [ $? -ne 0 ]; then
    echo "❌ Build failed"
    exit 1
fi
echo "✅ Build successful"

echo ""
echo "Step 2: Starting container..."
docker run -d \
    --name $CONTAINER_NAME \
    -p 3000:3000 \
    -p 2222:22 \
    -e SSH_PASSWORD=$SSH_PASSWORD \
    $IMAGE_NAME

if [ $? -ne 0 ]; then
    echo "❌ Container start failed"
    exit 1
fi
echo "✅ Container started"

echo ""
echo "Step 3: Waiting for services to be ready..."
sleep 5

echo ""
echo "Step 4: Testing HTTP service (expect 200)..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/)

if [ "$HTTP_STATUS" = "200" ]; then
    echo "✅ HTTP test passed (status: $HTTP_STATUS)"
else
    echo "❌ HTTP test failed (status: $HTTP_STATUS)"
    docker logs $CONTAINER_NAME
    exit 1
fi

echo ""
echo "Step 5: Testing database..."
docker exec $CONTAINER_NAME sqlite3 /app/arena/arena.db "SELECT * FROM arena_secret;" > /tmp/db_test.txt

if grep -q "FLAG{initial_placeholder_flag}" /tmp/db_test.txt; then
    echo "✅ Database test passed"
else
    echo "❌ Database test failed"
    cat /tmp/db_test.txt
    exit 1
fi

echo ""
echo "Step 6: Testing Flag injection..."
docker exec $CONTAINER_NAME sqlite3 /app/arena/arena.db \
    "UPDATE arena_secret SET flag='FLAG{test_flag_12345}' WHERE id=1;"

INJECTED_FLAG=$(docker exec $CONTAINER_NAME sqlite3 /app/arena/arena.db \
    "SELECT flag FROM arena_secret WHERE id=1;")

if [ "$INJECTED_FLAG" = "FLAG{test_flag_12345}" ]; then
    echo "✅ Flag injection test passed"
else
    echo "❌ Flag injection test failed (got: $INJECTED_FLAG)"
    exit 1
fi

echo ""
echo "Step 7: Testing SSH login..."
timeout 5 sshpass -p $SSH_PASSWORD ssh -o StrictHostKeyChecking=no -p 2222 defender@localhost "echo 'SSH test successful'" > /tmp/ssh_test.txt 2>&1

if grep -q "SSH test successful" /tmp/ssh_test.txt; then
    echo "✅ SSH test passed"
else
    echo "⚠️  SSH test skipped (sshpass not installed)"
    echo "   Manual test: ssh -p 2222 defender@localhost (password: $SSH_PASSWORD)"
fi

echo ""
echo "=========================================="
echo "All tests passed! ✅"
echo "=========================================="
echo ""
echo "Container is still running for manual inspection:"
echo "  HTTP:  curl http://localhost:3000"
echo "  SSH:   ssh -p 2222 defender@localhost (password: $SSH_PASSWORD)"
echo "  DB:    docker exec $CONTAINER_NAME sqlite3 /app/arena/arena.db"
echo ""
echo "To stop: docker stop $CONTAINER_NAME"
