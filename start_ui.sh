#!/usr/bin/env bash
# Start Bloomberg Terminal UI — FastAPI backend + Vite frontend
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 Starting Bloomberg Terminal..."
echo ""

# Start FastAPI backend
echo "▶ Starting FastAPI backend on http://localhost:8000"
cd "$ROOT"
source .venv/bin/activate
uvicorn api.main:app --reload --port 8000 &
BACKEND_PID=$!

# Start Vite frontend
echo "▶ Starting Vite frontend on http://localhost:5173"
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✅ Bloomberg Terminal running:"
echo "   Frontend: http://localhost:5173"
echo "   API docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
