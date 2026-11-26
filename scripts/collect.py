"""Recoge datos de SOLO LECTURA (sin claves): W3 (API pública de Hyperliquid), W2 (Arbitrum: USDC + Aave v3 por RPC público),
aportes/retiradas USDC entre W1 y W2 (→ participaciones) y copia del estado del bot (repo privado, token de solo lectura)."""
import os, csv, json, math, datetime as dt
import requests

HL = "https://api.hyperliquid.xyz/info"
ARB = os.environ.get("ARB_RPC") or "https://arb1.arbitrum.io/rpc"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"      # USDC nativo en Arbitrum One
POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"      # Aave v3 Pool en Arbitrum One
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
E = lambda k: (os.environ.get(k) or "").strip()
W1, W2, W3, BOT_REPO, TOKEN = E("W1_ADDRESS"), E("W2_ADDRESS"), E("W3_ADDRESS"), E("BOT_REPO"), E("BOT_TOKEN")
FILES = ["estado_bot.json", "historial_operaciones.csv", "historial_fills.csv", "historial_reglas.csv", "indicadores_diarios.csv"]
os.makedirs("data/bot", exist_ok=True)


def hl(body):
    r = requests.post(HL, json=body, timeout=30); r.raise_for_status(); return r.json()


def rpc(method, params):
    j = requests.post(ARB, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=30).json()
    if "error" in j: raise RuntimeError(f"RPC {method}: {j['error']}")
    return j["result"]


pad = lambda a: a.lower().replace("0x", "").rjust(64, "0")
call = lambda to, data: rpc("eth_call", [{"to": to, "data": data}, "latest"])


def w2_value(a):
    """USDC libre + (colateral − deuda) en Aave v3, en USD. Otros tokens fuera de Aave NO se cuentan."""
    usdc = int(call(USDC, "0x70a08231" + pad(a)), 16) / 1e6
    h = call(POOL, "0xbf92857c" + pad(a))[2:]                    # getUserAccountData: colateral, deuda (USD, 8 dec)
    col, debt = int(h[0:64], 16) / 1e8, int(h[64:128], 16) / 1e8
    return usdc + col - debt, debt


def block_at(ts, hi):
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if int(rpc("eth_getBlockByNumber", [hex(mid), False])["timestamp"], 16) < ts: lo = mid + 1
        else: hi = mid
    return lo


def flows(a, b):
    """Transferencias de USDC W1→W2 (aporte, +) y W2→W1 (retirada, −) entre los bloques a..b."""
    out = []
    for src, dst, sg in ((W1, W2, 1), (W2, W1, -1)):
        for s in range(a, b + 1, 20000):
            f = {"fromBlock": hex(s), "toBlock": hex(min(s + 19999, b)), "address": USDC, "topics": [TRANSFER, "0x" + pad(src), "0x" + pad(dst)]}
            for lg in rpc("eth_getLogs", [f]):
                ts = int(rpc("eth_getBlockByNumber", [lg["blockNumber"], False])["timestamp"], 16)
                out.append({"key": f"{lg['transactionHash']}:{int(lg['logIndex'], 16)}", "ts": ts, "block": int(lg["blockNumber"], 16),
                            "usdc": sg * int(lg["data"], 16) / 1e6})
    return sorted(out, key=lambda x: (x["block"], x["key"]))


def copy_bot_state():
    if not (BOT_REPO and TOKEN): return
    h = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github.raw+json"}
    got = {}
    for f in FILES:
        r = requests.get(f"https://api.github.com/repos/{BOT_REPO}/contents/state/{f}", headers=h, timeout=30)
        if r.status_code == 200: got[f] = r.content
    st = json.loads(got["estado_bot.json"]) if "estado_bot.json" in got else {}
    if st.get("network") != "mainnet" and E("ALLOW_TESTNET") != "1":
        print("El bot está en testnet: NO se publica (ALLOW_TESTNET=1 solo para pruebas)."); return
    for f, c in got.items(): open(f"data/bot/{f}", "wb").write(c)


def snapshot():
    if not W3: print("Falta el secret W3_ADDRESS"); return
    btc = float(hl({"type": "allMids"})["BTC"])
    bal = {b["coin"]: float(b["total"]) for b in hl({"type": "spotClearinghouseState", "user": W3}).get("balances", [])}
    w3 = bal.get("USDC", 0) + bal.get("UBTC", 0) * btc + float(hl({"type": "clearinghouseState", "user": W3})["marginSummary"]["accountValue"])
    w2, debt, fo = None, 0.0, json.load(open("data/fondo.json"))
    new_ev = []
    if W1 and W2:                                                   # si W2 falla, el workflow falla: no se publica un NAV incompleto
        w2, debt = w2_value(W2)
        latest = int(rpc("eth_blockNumber", []), 16)
        start = (fo["ultimo_bloque"] + 1) if fo.get("ultimo_bloque") else block_at(int(dt.datetime.fromisoformat(fo["inicio"]).timestamp()), latest)
        done = {r["tx"] for r in csv.DictReader(open("data/flujos.csv"))} if os.path.exists("data/flujos.csv") else set()
        new_ev = [e for e in flows(start, latest) if e["key"] not in done]
        fo["ultimo_bloque"] = latest
    nav = w3 + (w2 or 0)
    sh = fo["participaciones"]
    if new_ev:                                                      # precio = NAV antes del flujo / participaciones (o precio inicial)
        px = (nav - sum(e["usdc"] for e in new_ev)) / sh if sh > 0 else fo["precio_inicial"]
        new = not os.path.exists("data/flujos.csv")
        with open("data/flujos.csv", "a", newline="") as fh:
            w = csv.writer(fh)
            if new: w.writerow(["t", "tx", "tipo", "usdc", "precio_participacion", "participaciones", "participaciones_total"])
            for e in new_ev:
                n = math.floor(e["usdc"] / px + 1e-9) if e["usdc"] > 0 else -min(sh, math.ceil(-e["usdc"] / px - 1e-9))
                sh += n
                w.writerow([dt.datetime.fromtimestamp(e["ts"], dt.timezone.utc).isoformat(), e["key"], "aporte" if n > 0 else "retirada",
                            round(e["usdc"], 6), round(px, 6), n, sh])
        fo["participaciones"] = sh
    spx = round(nav / sh, 6) if sh > 0 else fo["precio_inicial"]
    cols = ["t", "btc_px", "w2_usd", "w3_usd", "w4_usd", "debt_usd", "nav_usd", "shares", "share_px"]
    row = {"t": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "btc_px": btc, "w2_usd": "" if w2 is None else round(w2, 2),
           "w3_usd": round(w3, 2), "w4_usd": "", "debt_usd": round(debt, 2), "nav_usd": round(nav, 2), "shares": sh, "share_px": spx}
    first = not os.path.exists("data/snapshots.csv")
    with open("data/snapshots.csv", "a", newline="") as fh:
        wr = csv.DictWriter(fh, cols)
        if first: wr.writeheader()
        wr.writerow(row)
    json.dump(fo, open("data/fondo.json", "w"), indent=1)
    print(row, f"· {len(new_ev)} flujos nuevos")


if __name__ == "__main__":
    copy_bot_state()
    snapshot()
