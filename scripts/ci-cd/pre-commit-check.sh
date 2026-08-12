#!/bin/bash

# Pre-commit quality check for Aitana Platform v6 (monorepo)
# Runs frontend + backend checks to match CI environment

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

# Ensure we're at project root
if [ ! -f "cloudbuild.yaml" ] || [ ! -d "frontend" ] || [ ! -d "backend" ]; then
    echo -e "${RED}Error: Run this script from the platform root directory${NC}"
    exit 1
fi

echo "=============================================="
echo "Pre-commit Quality Check (v6 Monorepo)"
echo "=============================================="

# --- Frontend ---
echo ""
echo "=============================================="
echo "1. FRONTEND QUALITY CHECKS"
echo "=============================================="

cd frontend

echo -e "${YELLOW}Running frontend linting...${NC}"
npm run lint
echo -e "${GREEN}  Linting passed${NC}"

echo -e "${YELLOW}Running frontend TypeScript check...${NC}"
npx tsc --noEmit
echo -e "${GREEN}  TypeScript check passed${NC}"

echo -e "${YELLOW}Running frontend tests...${NC}"
npm run test:run
echo -e "${GREEN}  Tests passed${NC}"

cd ..

# --- Backend ---
echo ""
echo "=============================================="
echo "2. BACKEND QUALITY CHECKS"
echo "=============================================="

cd backend

if [ ! -d ".venv" ]; then
    echo -e "${RED}Backend venv not found. Run 'make install' first.${NC}"
    exit 1
fi

echo -e "${YELLOW}Running backend linting...${NC}"
make lint
echo -e "${GREEN}  Linting passed${NC}"

echo -e "${YELLOW}Running backend tests...${NC}"
make test-fast
echo -e "${GREEN}  Tests passed${NC}"

cd ..

echo ""
echo "=============================================="
echo -e "${GREEN}ALL QUALITY CHECKS PASSED${NC}"
echo "=============================================="
echo "  Frontend: lint, typecheck, tests"
echo "  Backend:  lint, tests (fast)"
echo ""
echo -e "${GREEN}Ready for commit${NC}"
