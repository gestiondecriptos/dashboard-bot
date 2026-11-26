# Dashboard Bot BTC · Hyperliquid (solo lectura)
Sin claves, sin órdenes. Fase A: Inicio, Operaciones, Reglas, Métricas. Sin datos reales muestra una DEMO.

## Pasos (todo en la web de GitHub)
1. **Crear repo público** `dashboard-bot` → *Add file → Upload files*: sube `index.html`, `README.md`, carpetas `scripts/`, `data/` y `.github/workflows/actualizar.yml` (arrastra las carpetas).
2. **Pages:** Settings → Pages → *Deploy from a branch* → `main` / `(root)`. Tu web: `https://TU_USUARIO.github.io/dashboard-bot/`.
3. **Secret `W3_ADDRESS`:** Settings → Secrets and variables → Actions → New secret = dirección pública de W3 (la cuenta principal de Hyperliquid, no la clave de agente).
4. **Token de lectura del repo del bot:** GitHub → Settings → Developer settings → Fine-grained tokens → *Only select repositories* = repo del bot; permiso **Contents: Read-only**. Guárdalo como secret `BOT_TOKEN`. En Variables crea `BOT_REPO` = `TU_USUARIO/nombre-repo-bot`.
5. Actions → *Actualizar datos del dashboard* → Run workflow. En 1-2 min aparece `data/snapshots.csv` y la demo desaparece.
6. En `index.html` cambia `METRICAS_URL` por la URL de tu web de métricas.
- Mientras el bot esté en **testnet** no se copia nada de él (solo mainnet). Para probar con testnet: variable `ALLOW_TESTNET=1` (publicaría datos de prueba).
- W2/W4 y participaciones: llegan en la fase B (necesitan sus direcciones y `data/fondo.json`).
