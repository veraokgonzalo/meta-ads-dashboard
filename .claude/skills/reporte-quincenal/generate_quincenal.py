#!/usr/bin/env python3
"""
Informe quincenal de portfolio (Meta Ads) — últimos 15 días, todas las cuentas.

Flujo en 3 pasos:
  1) fetch  -> consulta la Graph API (todas las cuentas de me/adaccounts) y vuelca
               las estadísticas de los últimos 15 días a data/quincenal_<hasta>.json
               (+ data/quincenal_latest.json). También deja un esqueleto editable de
               sugerencias en data/quincenal_<hasta>_sugerencias.json si no existe.
  2) (Claude) lee el JSON de datos, analiza y completa el JSON de sugerencias:
               resumen de alto nivel del portfolio + por cuenta + sugerencias de
               anuncios/creativos.
  3) build  -> hornea un único HTML consolidado en reports/quincenales/.

Uso:
  python generate_quincenal.py fetch [--days 15] [--no-thumbs]
  python generate_quincenal.py build [--data <archivo.json>] [--suggestions <archivo.json>]

Requiere META_TOKEN en el .env del proyecto (igual que server.py / reporte-cuenta).
"""
import argparse
import base64
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API = "https://graph.facebook.com/v21.0"
SKILL_DIR = Path(__file__).parent
PROJECT_DIR = SKILL_DIR.parent.parent.parent  # .claude/skills/reporte-quincenal -> proyecto
LOGO_PATH = PROJECT_DIR / "assets" / "logo_tras_amarillo.jpg"
TEMPLATE_PATH = SKILL_DIR / "report_template.html"
DATA_DIR = PROJECT_DIR / "data"
OUT_DIR = PROJECT_DIR / "reports" / "quincenales"

MSG_ACTION = "onsite_conversion.messaging_conversation_started_7d"
MONTHS_ES = ["", "ene", "feb", "mar", "abr", "may", "jun",
             "jul", "ago", "sep", "oct", "nov", "dic"]

# Objetivos de campaña -> tipo del anuncio.
MSG_OBJECTIVES = {"OUTCOME_ENGAGEMENT", "MESSAGES", "CONVERSATIONS"}
TRAFFIC_OBJECTIVES = {"OUTCOME_TRAFFIC", "LINK_CLICKS", "TRAFFIC"}


# ── token / .env ────────────────────────────────────────────────────────────
def read_env_var(name: str, required: bool = True) -> str:
    env = PROJECT_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line[len(name) + 1:].strip().strip('"').strip("'")
    if required:
        sys.exit(f"ERROR: {name} no encontrado en {env}")
    return ""


# ── helpers ─────────────────────────────────────────────────────────────────
def to_num(v):
    if v in (None, "", "Not available"):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def extract_action(insight, action_type):
    for a in insight.get("actions", []) or []:
        if a.get("action_type") == action_type:
            return to_num(a.get("value"))
    return 0.0


# Nomenclatura de campañas: PERIODO_CAMPANA_DESTINO (ej. MAY26_LOCALIZADA_WAPP)
DEST_MAP = {
    "WAPP": "Mensajes", "MSG": "Mensajes", "DM": "Mensajes", "MENSAJES": "Mensajes",
    "WEB": "Tráfico", "TRAFICO": "Tráfico", "LINK": "Tráfico", "SITE": "Tráfico",
    "LANDING": "Tráfico", "LEADS": "Leads", "LEAD": "Leads",
    "PERFIL": "Perfil", "PROFILE": "Perfil",
}
CAMP_ALIAS = {"DIAMADRE": "Día de la Madre"}
NOISE = {"ADSET", "NEW", "NUEVOVIDEO", "NUEVOVID", "NUEVO", "NUEVA", "COPY", "COPIA"}
TYPE_ITEMS = [
    ("REEL", "Reel"), ("CARRUSEL", "Carrusel"), ("CAROUSEL", "Carrusel"),
    ("PLACA", "Placa"), ("POST", "Placa"), ("IMAGEN", "Placa"),
    ("IMG", "Placa"), ("FOTO", "Placa"), ("VIDEO", "Video"),
]


def _is_noise(token):
    u = token.upper()
    return u in NOISE or bool(re.fullmatch(r"V?\d+", u))


def parse_campaign(name):
    """PERIODO_CAMPANA_DESTINO -> 'Campaña [Destino]' (destino solo si no es Mensajes)."""
    parts = [p for p in (name or "").split("_") if p]
    if len(parts) < 2:
        return name or "—"
    rest = parts[1:]
    while len(rest) > 1 and _is_noise(rest[-1]):
        rest = rest[:-1]
    dest = ""
    if len(rest) > 1 and rest[-1].upper() in DEST_MAP:
        dest = DEST_MAP[rest[-1].upper()]
        rest = rest[:-1]
    kept = [t for t in rest if not _is_noise(t)] or rest[:1]
    camp = " ".join(CAMP_ALIAS.get(t.upper(), t.capitalize()) for t in kept)
    if dest and dest != "Mensajes":
        return f"{camp} {dest}"
    return camp


def parse_type(ad_name):
    up = (ad_name or "").upper()
    for key, label in TYPE_ITEMS:
        if key in up:
            return label
    return ""


def api_get(path, params, token):
    params = dict(params, access_token=token)
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    if "error" in data:
        e = data["error"]
        sys.exit(f"ERROR Graph API: {e.get('message')} (codigo {e.get('code')})")
    return data


def api_get_all(path, params, token):
    rows, params = [], dict(params, access_token=token)
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    while url:
        with urllib.request.urlopen(url, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        if "error" in data:
            e = data["error"]
            sys.exit(f"ERROR Graph API: {e.get('message')} (codigo {e.get('code')})")
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
    return rows


def b64_data_uri(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def fetch_thumb(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
        return b64_data_uri(raw, mime)
    except Exception:
        return ""


def period_label(since: str, until: str) -> str:
    d0 = date.fromisoformat(since)
    d1 = date.fromisoformat(until)
    if d0.month == d1.month:
        return f"{d0.day}–{d1.day} {MONTHS_ES[d1.month]} {d1.year}"
    return f"{d0.day} {MONTHS_ES[d0.month]} – {d1.day} {MONTHS_ES[d1.month]} {d1.year}"


# ── FETCH ─────────────────────────────────────────────────────────────────────
def ad_type_of(objective: str, messages: float, clicks: float) -> str:
    if objective in MSG_OBJECTIVES:
        return "mensajes"
    if objective in TRAFFIC_OBJECTIVES:
        return "trafico"
    return "mensajes" if messages > 0 else "trafico"


def fetch_account(acct, since, until, token, with_thumbs):
    acct_id = acct["id"].replace("act_", "")
    name = acct.get("name") or f"act_{acct_id}"
    currency = acct.get("currency", "ARS")
    time_range = json.dumps({"since": since, "until": until})
    print(f"  · {name} (act_{acct_id})")

    # Mapa campaña_id -> objetivo (para clasificar tipo del anuncio)
    obj_map = {}
    try:
        for c in api_get_all(f"act_{acct_id}/campaigns",
                             {"fields": "id,objective", "limit": 500}, token):
            obj_map[c["id"]] = c.get("objective", "")
    except SystemExit:
        raise
    except Exception:
        pass

    ads_raw = api_get_all(
        f"act_{acct_id}/insights",
        {"level": "ad", "time_range": time_range,
         "fields": "ad_id,ad_name,campaign_id,campaign_name,adset_name,"
                   "spend,impressions,clicks,ctr,reach,frequency,actions",
         "limit": 500},
        token,
    )

    thumbs = {}
    if with_thumbs and ads_raw:
        try:
            for ad in api_get_all(
                f"act_{acct_id}/ads",
                {"fields": "id,creative{thumbnail_url}", "time_range": time_range,
                 "thumbnail_width": 160, "thumbnail_height": 160, "limit": 500},
                token,
            ):
                url = (ad.get("creative") or {}).get("thumbnail_url")
                if url:
                    thumbs[ad["id"]] = url
        except Exception:
            pass

    cache, ads = {}, []
    for r in ads_raw:
        spend = to_num(r.get("spend"))
        if spend <= 0:
            continue
        clicks = to_num(r.get("clicks"))
        msgs = extract_action(r, MSG_ACTION)
        atype = ad_type_of(obj_map.get(r.get("campaign_id"), ""), msgs, clicks)
        metric = msgs if atype == "mensajes" else clicks
        thumb = ""
        u = thumbs.get(r.get("ad_id"))
        if u:
            if u not in cache:
                cache[u] = fetch_thumb(u)
            thumb = cache[u]
        ads.append({
            "ad_id": r.get("ad_id"),
            "name": r.get("ad_name", ""),
            "campaign": parse_campaign(r.get("campaign_name", "")),
            "campaign_raw": r.get("campaign_name", ""),
            "ad_type_creative": parse_type(r.get("ad_name", "")),
            "type": atype,
            "spend": spend,
            "impressions": to_num(r.get("impressions")),
            "clicks": clicks,
            "ctr": to_num(r.get("ctr")),
            "reach": to_num(r.get("reach")),
            "frequency": to_num(r.get("frequency")),
            "messages": msgs,
            "metric": metric,
            "cost": (spend / metric) if metric else 0.0,
            "thumb": thumb,
        })
    ads.sort(key=lambda a: a["spend"], reverse=True)

    spend = sum(a["spend"] for a in ads)
    impr = sum(a["impressions"] for a in ads)
    clicks = sum(a["clicks"] for a in ads)
    msgs = sum(a["messages"] for a in ads)
    has_msg = any(a["type"] == "mensajes" for a in ads)
    has_traf = any(a["type"] == "trafico" for a in ads)
    return {
        "id": acct_id,
        "name": name,
        "currency": currency,
        "status": acct.get("account_status"),
        "has_messages": has_msg,
        "has_traffic": has_traf,
        "totals": {
            "spend": spend,
            "impressions": impr,
            "clicks": clicks,
            "messages": msgs,
            "ctr": (clicks / impr * 100) if impr else 0.0,
            "cost_per_msg": (spend / msgs) if msgs else 0.0,
            "cost_per_click": (spend / clicks) if clicks else 0.0,
        },
        "ads": ads,
    }


def cmd_fetch(args):
    token = read_env_var("META_TOKEN")
    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=args.days - 1)
    since_s, until_s = since.isoformat(), until.isoformat()
    print(f"Periodo: {since_s} a {until_s} ({args.days} días)")

    accounts = api_get_all(
        "me/adaccounts",
        {"fields": "id,name,account_status,currency", "limit": 200}, token)
    print(f"Cuentas en el portfolio: {len(accounts)}")

    out = []
    for acct in accounts:
        acc = fetch_account(acct, since_s, until_s, token, not args.no_thumbs)
        if acc["totals"]["spend"] > 0:
            out.append(acc)
    out.sort(key=lambda a: a["totals"]["spend"], reverse=True)

    data = {
        "since": since_s,
        "until": until_s,
        "days": args.days,
        "period_label": period_label(since_s, until_s),
        "generated": date.today().isoformat(),
        "accounts": out,
    }
    DATA_DIR.mkdir(exist_ok=True)
    data_path = DATA_DIR / f"quincenal_{until_s}.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "quincenal_latest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDatos -> {data_path}  ({len(out)} cuentas con inversión)")

    # Esqueleto de sugerencias (no pisa uno existente)
    sug_path = DATA_DIR / f"quincenal_{until_s}_sugerencias.json"
    if not sug_path.exists():
        skeleton = {
            "summary": "[Editar: resumen de alto nivel del portfolio]",
            "accounts": {
                a["id"]: {
                    "name": a["name"],
                    "summary": "[Editar: resumen de la cuenta]",
                    "suggestions": [
                        {"severity": "media", "ad": "", "text": "[Editar: sugerencia]"}
                    ],
                } for a in out
            },
        }
        sug_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Esqueleto de sugerencias -> {sug_path}")
    print("\nSiguiente paso: completar el JSON de sugerencias y correr 'build'.")


# ── BUILD ─────────────────────────────────────────────────────────────────────
def cmd_build(args):
    data_path = Path(args.data) if args.data else (DATA_DIR / "quincenal_latest.json")
    if not data_path.exists():
        sys.exit(f"ERROR: no existe {data_path}. Corré 'fetch' primero.")
    data = json.loads(data_path.read_text(encoding="utf-8"))

    sug_path = (Path(args.suggestions) if args.suggestions
                else DATA_DIR / f"quincenal_{data['until']}_sugerencias.json")
    suggestions = {}
    if sug_path.exists():
        suggestions = json.loads(sug_path.read_text(encoding="utf-8"))
    else:
        print(f"AVISO: sin archivo de sugerencias ({sug_path}); se usan placeholders.")

    # Fusiona sugerencias dentro de cada cuenta
    sacc = suggestions.get("accounts", {})
    for a in data["accounts"]:
        s = sacc.get(a["id"], {})
        a["analysis"] = s.get("summary", "[Pendiente de análisis]")
        a["suggestions"] = s.get("suggestions", [])
    data["summary"] = suggestions.get("summary", "[Pendiente de análisis]")

    # Totales del portfolio. La inversión se agrupa por moneda (no se mezclan CLP/ARS).
    accs = data["accounts"]
    by_cur = {}
    for a in accs:
        by_cur[a["currency"]] = by_cur.get(a["currency"], 0.0) + a["totals"]["spend"]
    data["portfolio"] = {
        "n_accounts": len(accs),
        "messages": sum(a["totals"]["messages"] for a in accs),
        "clicks": sum(a["totals"]["clicks"] for a in accs),
        "impressions": sum(a["totals"]["impressions"] for a in accs),
        "spend_by_currency": by_cur,
    }
    imp = data["portfolio"]["impressions"]
    data["portfolio"]["ctr"] = (data["portfolio"]["clicks"] / imp * 100) if imp else 0.0

    # Logo
    logo = ""
    if LOGO_PATH.exists():
        logo = b64_data_uri(LOGO_PATH.read_bytes(), "image/jpeg")
    data["logo"] = logo

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = template.replace("/*__DATA__*/{}", json.dumps(data, ensure_ascii=False))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"quincenal_{data['until']}.html"
    out.write_text(html, encoding="utf-8")
    print(f"OK -> {out}")


def main():
    ap = argparse.ArgumentParser(description="Informe quincenal de portfolio Meta Ads")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="Trae stats de los últimos N días (todas las cuentas)")
    f.add_argument("--days", type=int, default=15, help="Ventana en días (default 15)")
    f.add_argument("--no-thumbs", action="store_true", help="No descargar miniaturas")
    f.set_defaults(func=cmd_fetch)

    b = sub.add_parser("build", help="Hornea el HTML consolidado")
    b.add_argument("--data", default=None, help="JSON de datos (default quincenal_latest.json)")
    b.add_argument("--suggestions", default=None, help="JSON de sugerencias")
    b.set_defaults(func=cmd_build)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
