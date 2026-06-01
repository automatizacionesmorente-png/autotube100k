#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "🎬 AutoTube — Arrancando..."

# Crear entorno virtual si no existe
if [ ! -d ".venv" ]; then
  echo "📦 Creando entorno virtual Python..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "📦 Instalando dependencias..."
pip install -q -r requirements.txt

# Verificar FFmpeg
if ! command -v ffmpeg &>/dev/null; then
  echo "⚠️  FFmpeg no encontrado. Instalando..."
  if [[ "$OSTYPE" == "darwin"* ]]; then
    brew install ffmpeg
  else
    apt-get install -y ffmpeg 2>/dev/null || echo "Instala FFmpeg manualmente: sudo apt install ffmpeg"
  fi
fi

# Crear directorios
mkdir -p output data

echo ""
echo "✅ Listo. Abriendo en http://localhost:8000"
echo ""

python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
