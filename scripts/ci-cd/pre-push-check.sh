#!/bin/bash

# Pre-push check for Aitana Platform v6
# Runs the full quality suite including builds

echo "Running pre-push quality checks..."
echo ""

# Frontend full quality check (lint + typecheck + tests + build)
echo "--- Frontend ---"
cd frontend
npm run quality:check
if [ $? -ne 0 ]; then
  echo ""
  echo "Frontend quality checks failed!"
  echo "Run 'cd frontend && npm run quality:check' to debug"
  exit 1
fi
cd ..

# Backend lint + tests
echo ""
echo "--- Backend ---"
cd backend
make lint && make test-fast
if [ $? -ne 0 ]; then
  echo ""
  echo "Backend quality checks failed!"
  echo "Run 'cd backend && make lint && make test-fast' to debug"
  exit 1
fi
cd ..

echo ""
echo "All quality checks passed. Safe to push."
