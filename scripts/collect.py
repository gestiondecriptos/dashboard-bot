"""Recoge datos de SOLO LECTURA: saldos de W3 (API pública de Hyperliquid, sin claves) y copia del estado del bot
(repo privado, con un token de solo lectura). Añade una fila horaria a data/snapshots.csv."""
import os, csv, json, datetime as dt
import requests

HL = "https://api.hyperliquid.xyz/info"
E = os.environ.get
W3, BOT_REPO, TOKEN = (E("W3_ADDRESS") or "").strip(), (E("BOT_REPO") or "").strip(), (E("BOT_TOKEN") or "").strip()
FILES = ["estado_bot.json", "historial_operaciones.csv", "historial_fills.csv", "historial_reglas.csv", "indicadores_diarios.csv"]
os.makedirs("data/bot", exist_ok=True)


def hl(body):
    r = requests.post(HL, json=body, timeout=30); r.raise_for_status(); return r.json()


def copy_bot_state():
    if not (BOT_REPO and TOKEN):
        return
    h = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github.raw+json"}
    got = {}
    for f in FILES:
        r = requests.get(f"https://api.github.com/repos/{BOT_REPO}/contents/state/{f}", headers=h, timeout=30)
        if r.status_code == 200:
            got[f] = r.content
    st = json.loads(got["estado_bot.json"]) if "estado_bot.json" in got else {}
    if st.get("network") != "mainnet" and E("ALLOW_TESTNET") != "1":
        print("El bot está en testnet: NO se publica (pon ALLOW_TESTNET=1 solo para pruebas)."); return
    for f, c in got.items():
        open(f"data/bot/{f}", "wb").write(c)


def snapshot():
    if not W3:
        print("Falta el secret W3_ADDRESS"); return
    btc = float(hl({"type": "allMids"})["BTC"])
    bal = {b["coin"]: float(b["total"]) for b in hl({"type": "spotClearinghouseState", "user": W3}).get("balances", [])}
    perp = float(hl({"type": "clearinghouseState", "user": W3})["marginSummary"]["accountValue"])
    usd = bal.get("USDC", 0) + bal.get("UBTC", 0) * btc + perp
    t = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    cols = ["t", "btc_px", "w2_usd", "w3_usd", "w4_usd", "nav_usd", "shares", "share_px"]
    fo = json.load(open("data/fondo.json")) if os.path.exists("data/fondo.json") else {}   # fase B: participaciones
    sh = fo.get("participaciones")
    row = {"t": t, "btc_px": btc, "w2_usd": "", "w3_usd": round(usd, 2), "w4_usd": "", "nav_usd": round(usd, 2),
           "shares": sh or "", "share_px": round(usd / sh, 6) if sh else ""}
    new = not os.path.exists("data/snapshots.csv")
    with open("data/snapshots.csv", "a", newline="") as fh:
        w = csv.DictWriter(fh, cols)
        if new: w.writeheader()
        w.writerow(row)
    print(row)


if __name__ == "__main__":
    copy_bot_state()
    snapshot()
