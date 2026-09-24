#!/usr/bin/env bash
# Abre la interfaz en el navegador. Solo escucha en este equipo (localhost): los datos llevan DNI.
cd "$(dirname "$0")/.." || exit 1
exec conda run --no-capture-output -n ortools_env streamlit run interfaz/app.py \
    --server.address localhost --browser.gatherUsageStats false
