"""Recoge datos de SOLO LECTURA (sin claves): W3 (API pública de Hyperliquid), W2 (Arbitrum: USDC + Aave v3 por RPC público),
aportes/retiradas USDC entre W1 y W2 (→ participaciones) y copia del estado del bot (repo privado, token de solo lectura)."""
import os, re, csv, json, math, datetime as dt
import requests

HL = "https://api.hyperliquid.xyz/info"
ARB = os.environ.get("ARB_RPC") or "https://arb1.arbitrum.io/rpc"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"      # USDC nativo en Arbitrum One
POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"      # Aave v3 Pool en Arbitrum One
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
E = lambda k: (os.environ.get(k) or "").strip()
W1, W2, W3, BOT_REPO, TOKEN = E("W1_ADDRESS"), E("W2_ADDRESS"), E("W3_ADDRESS"), E("BOT_REPO"), E("BOT_TOKEN")
STATE_DIR = E("STATE_DIR") or "state/mainnet"      # carpeta del estado del bot en su repo
FILES = ["estado_bot.json", "historial_operaciones.csv", "historial_fills.csv", "historial_reglas.csv", "indicadores_diarios.csv"]
ADDR = re.compile(rb"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")   # el repo es público: se ocultan direcciones en lo que se copia del bot
os.makedirs("data/bot", exist_ok=True)


def hl(body):
    r = requests.post(HL, json=body, timeout=30); r.raise_for_status(); return r.json()


def rpc(method, params):
    j = requests.post(ARB, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=30).json()
    if "error" in j: raise RuntimeError(f"RPC {method}: {j['error']}")
    return j["result"]


pad = lambda a: a.lower().replace("0x", "").rjust(64, "0")
call = lambda to, data: rpc("eth_call", [{"to": to, "data": data}, "latest"])


TOK = {"USDC": (USDC, 6), "USDT": ("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6), "WBTC": ("0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", 8)}
ZERO = "0x" + "0" * 40
FLOW_COLS = ["t", "tx", "tipo", "usdc", "precio_participacion", "participaciones", "participaciones_total", "token", "cantidad"]
SNAP_COLS = ["t", "btc_px", "w2_usd", "w3_usd", "w4_usd", "debt_usd", "nav_usd", "shares", "share_px", "btc_total", "usdc_total", "w3_arb_usd"]


def bal(tok, a):
    return int(call(tok, "0x70a08231" + pad(a)), 16)


def arb_detail(a):
    """Por token (USDC, USDT, WBTC) en una wallet de Arbitrum: libre, colateral (aToken de Aave) y deuda (variable + estable), en unidades del token."""
    d = {}
    for s, (t, dec) in TOK.items():
        w = call(POOL, "0x35ea6a75" + pad(t))[2:]
        at, st, va = ("0x" + w[i * 64:(i + 1) * 64][-40:] for i in (8, 9, 10))
        d[s] = {"libre": bal(t, a) / 10 ** dec, "colateral": bal(at, a) / 10 ** dec,
                "deuda": sum(bal(x, a) for x in (st, va) if x != ZERO) / 10 ** dec}
    return d


def arb_wallet(a, btc, k):
    """Cartera en Arbitrum en USD: USDC (USDC+USDT libres), BTC (WBTC libre), colateral (Aave), deuda. neto = suma - deuda."""
    d, pr = arb_detail(a), {"USDC": 1.0, "USDT": k, "WBTC": btc}
    usdc = sum(d[x]["libre"] * pr[x] for x in ("USDC", "USDT")); b = d["WBTC"]["libre"] * btc
    col = sum(d[x]["colateral"] * pr[x] for x in d); deb = sum(d[x]["deuda"] * pr[x] for x in d)
    net = lambda x: d[x]["libre"] + d[x]["colateral"] - d[x]["deuda"]
    return {"usdc": usdc, "btc": b, "colateral": col, "deuda": deb, "neto": usdc + b + col - deb,
            "usdc_u": net("USDC") + net("USDT") * k, "btc_u": net("WBTC")}


def hl_wallet(user, btc, k):
    """W3 en Hyperliquid: spot + margen perp + préstamos (borrowLend: suministrado = colateral, prestado = deuda).
    En cuenta unificada / portfolio margin el saldo vive en spot y el perp NO se suma (sería contar doble)."""
    mode = hl({"type": "userAbstraction", "user": user})
    mode = mode if isinstance(mode, str) else json.dumps(mode)
    spot = {b["coin"]: float(b["total"]) for b in hl({"type": "spotClearinghouseState", "user": user}).get("balances", [])}
    perp = 0.0 if mode in ("unifiedAccount", "portfolioMargin") else float(hl({"type": "clearinghouseState", "user": user})["marginSummary"]["accountValue"])
    bl = hl({"type": "borrowLendUserState", "user": user})
    print("HL modo:", mode, "| borrowLend:", json.dumps(bl))
    names = {t["index"]: t["name"] for t in hl({"type": "spotMeta"})["tokens"]}
    pr = {"USDC": 1.0, "USDT0": k, "UBTC": btc}
    sup, bor = dict.fromkeys(pr, 0.0), dict.fromkeys(pr, 0.0)
    for idx, st in bl.get("tokenToState", []):
        n = names.get(idx)
        if n not in pr: raise RuntimeError(f"borrowLend con un token sin valorar: {n or idx}")   # no se publica un NAV incompleto
        sup[n] += float(st["supply"]["value"]); bor[n] += float(st["borrow"]["value"])
    otros = sorted(c for c, v in spot.items() if v and c not in pr)
    if otros: print("AVISO: saldos spot en Hyperliquid sin valorar:", otros)
    usdc, ub = spot.get("USDC", 0) + spot.get("USDT0", 0) * k, spot.get("UBTC", 0)
    col = perp + sum(sup[n] * pr[n] for n in pr); deb = sum(bor[n] * pr[n] for n in pr)
    return {"usdc": usdc, "btc": ub * btc, "colateral": col, "deuda": deb, "neto": usdc + ub * btc + col - deb,
            "usdc_u": usdc + perp + (sup["USDC"] - bor["USDC"]) + (sup["USDT0"] - bor["USDT0"]) * k, "btc_u": ub + sup["UBTC"] - bor["UBTC"],
            "mode": mode, "avisos": ["Saldos sin valorar: " + ", ".join(otros)] if otros else []}


def w2_assets(a):
    """Por activo (USDC, USDT, WBTC): neto = libre + aToken − deuda (variable y estable), y deuda; en unidades del token."""
    net, debt = {}, {}
    for s, (t, d) in TOK.items():
        w = call(POOL, "0x35ea6a75" + pad(t))[2:]                   # getReserveData: palabras 8, 9, 10 = aToken, deuda estable, deuda variable
        at, st, va = ("0x" + w[i * 64:(i + 1) * 64][-40:] for i in (8, 9, 10))
        dbt = sum(bal(x, a) for x in (st, va) if x != ZERO)
        net[s] = (bal(t, a) + bal(at, a) - dbt) / 10 ** d
        debt[s] = dbt / 10 ** d
    return net, debt


def usdt_usdc():
    """USDT valorado en USDC (Kraken). Si falla, 1.0."""
    try:
        r = requests.get("https://api.kraken.com/0/public/Ticker?pair=USDTUSD,USDCUSD", timeout=20).json()["result"]
        p = {k[:4]: float(v["c"][0]) for k, v in r.items()}
        return p["USDT"] / p["USDC"]
    except Exception as ex:
        print("AVISO: precio USDT/USDC no disponible, uso 1.0:", ex); return 1.0


def ensure(path, cols):
    """Crea el CSV o le añade columnas nuevas (conserva las filas antiguas)."""
    rows = []
    if os.path.exists(path):
        if open(path).readline().strip().split(",") == cols: return
        rows = list(csv.DictReader(open(path)))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def block_at(ts, hi):
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if int(rpc("eth_getBlockByNumber", [hex(mid), False])["timestamp"], 16) < ts: lo = mid + 1
        else: hi = mid
    return lo


def flows(a, b):
    """Transferencias USDC/USDT/WBTC W1→W2 (aporte, +) y W2→W1 (retirada, −) entre los bloques a..b."""
    out = []
    for sym, (t, d) in TOK.items():
        for src, dst, sg in ((W1, W2, 1), (W2, W1, -1)):
            for x in range(a, b + 1, 20000):
                f = {"fromBlock": hex(x), "toBlock": hex(min(x + 19999, b)), "address": t, "topics": [TRANSFER, "0x" + pad(src), "0x" + pad(dst)]}
                for lg in rpc("eth_getLogs", [f]):
                    ts = int(rpc("eth_getBlockByNumber", [lg["blockNumber"], False])["timestamp"], 16)
                    out.append({"key": f"{lg['transactionHash']}:{int(lg['logIndex'], 16)}", "ts": ts, "block": int(lg["blockNumber"], 16),
                                "tok": sym, "amt": sg * int(lg["data"], 16) / 10 ** d})
    return sorted(out, key=lambda x: (x["block"], x["key"]))


def copy_bot_state():
    if not (BOT_REPO and TOKEN): return
    h = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github.raw+json"}
    got = {}
    for f in FILES:
        r = requests.get(f"https://api.github.com/repos/{BOT_REPO}/contents/{STATE_DIR}/{f}", headers=h, timeout=30)
        if r.status_code == 200: got[f] = r.content
    st = json.loads(got["estado_bot.json"]) if "estado_bot.json" in got else {}
    if st.get("network") != "mainnet" and E("ALLOW_TESTNET") != "1":
        print("El bot está en testnet: NO se publica (ALLOW_TESTNET=1 solo para pruebas)."); return
    for f, c in got.items(): open(f"data/bot/{f}", "wb").write(ADDR.sub(b"0x(oculta)", c))


def snapshot():
    if not W3: print("Falta el secret W3_ADDRESS"); return
    btc = float(hl({"type": "allMids"})["BTC"])
    k = usdt_usdc(); pr = {"USDC": 1.0, "USDT": k, "WBTC": btc}
    H, A, B = hl_wallet(W3, btc, k), arb_wallet(W3, btc, k), None      # USDT se cuenta como USDC; WBTC como BTC
    w2_usdc = w2_btc = debt = None; new_ev = []; fo = json.load(open("data/fondo.json"))
    if W1 and W2:                                                     # si W2 falla, el workflow falla: no se publica un NAV incompleto
        B = arb_wallet(W2, btc, k)
        w2_usdc, w2_btc, debt = B["usdc_u"], B["btc_u"], B["deuda"]
        latest = int(rpc("eth_blockNumber", []), 16)
        start = (fo["ultimo_bloque"] + 1) if fo.get("ultimo_bloque") else block_at(int(dt.datetime.fromisoformat(fo["inicio"]).timestamp()), latest)
        done = {r["tx"] for r in csv.DictReader(open("data/flujos.csv"))} if os.path.exists("data/flujos.csv") else set()
        new_ev = [e for e in flows(start, latest) if e["key"] not in done]
        for e in new_ev: e["usdc"] = e["amt"] * pr[e["tok"]]
        fo["ultimo_bloque"] = latest
    w2 = None if w2_usdc is None else w2_usdc + w2_btc * btc
    w3, w3_arb = H["neto"], A["neto"]                                 # W3 = Hyperliquid + Arbitrum, separadas
    nav = w3 + w3_arb + (w2 or 0)
    sh = fo["participaciones"]
    if new_ev:                                                        # precio = NAV antes del flujo / participaciones (o precio inicial)
        px = (nav - sum(e["usdc"] for e in new_ev)) / sh if sh > 0 else fo["precio_inicial"]
        ensure("data/flujos.csv", FLOW_COLS)
        with open("data/flujos.csv", "a", newline="") as fh:
            w = csv.DictWriter(fh, FLOW_COLS)
            for e in new_ev:
                n = math.floor(e["usdc"] / px + 1e-9) if e["usdc"] > 0 else -min(sh, math.ceil(-e["usdc"] / px - 1e-9))
                sh += n
                w.writerow({"t": dt.datetime.fromtimestamp(e["ts"], dt.timezone.utc).isoformat(), "tx": e["key"], "tipo": "aporte" if n > 0 else "retirada",
                            "usdc": round(e["usdc"], 6), "precio_participacion": round(px, 6), "participaciones": n, "participaciones_total": sh,
                            "token": e["tok"], "cantidad": e["amt"]})
        fo["participaciones"] = sh
    r = lambda v, d=2: "" if v is None else round(v, d)
    row = {"t": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "btc_px": btc, "w2_usd": r(w2), "w3_usd": r(w3), "w4_usd": "",
           "debt_usd": r((debt or 0) + H["deuda"] + A["deuda"]), "nav_usd": r(nav), "shares": sh, "share_px": round(nav / sh, 6) if sh > 0 else fo["precio_inicial"],
           "btc_total": round(H["btc_u"] + A["btc_u"] + (w2_btc or 0), 8), "usdc_total": round(H["usdc_u"] + A["usdc_u"] + (w2_usdc or 0), 2), "w3_arb_usd": r(w3_arb)}
    ensure("data/snapshots.csv", SNAP_COLS)
    with open("data/snapshots.csv", "a", newline="") as fh: csv.DictWriter(fh, SNAP_COLS).writerow(row)
    pub = lambda x: None if x is None else {q: round(x[q], 2) for q in ("usdc", "btc", "colateral", "deuda", "neto")}
    json.dump({"t": row["t"], "carteras": {"W2 Arbitrum": pub(B), "W3 Arbitrum": pub(A), "W3 Hyperliquid": pub(H), "W4 Bitcoin": None},
               "hl_modo": H["mode"], "avisos": H["avisos"]}, open("data/carteras.json", "w"), indent=1, ensure_ascii=False)
    json.dump(fo, open("data/fondo.json", "w"), indent=1)
    print(row, f"· {len(new_ev)} flujos nuevos")


if __name__ == "__main__":
    copy_bot_state()
    snapshot()
