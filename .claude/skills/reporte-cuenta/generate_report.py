#!/usr/bin/env python3
"""
Genera un informe HTML estatico de performance por cuenta de Meta Ads.

Consulta la Graph API usando META_TOKEN del .env (build-time), hornea los datos
en una plantilla HTML autocontenida (logo y miniaturas embebidos en base64;
Chart.js por CDN) y la escribe en reports/.

Uso:
  python generate_report.py --account <ID> --month 2026-05 --type mensajes
  python generate_report.py --account <ID> --month 2026-05 --type trafico

  --type mensajes  -> KPI/charts/tabla sobre "Mensajes" (conversaciones iniciadas)
  --type trafico   -> KPI/charts/tabla sobre "Clics" (campo clicks, todos los clics)
"""
import argparse
import base64
import calendar
import json
import mimetypes
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

API = "https://graph.facebook.com/v21.0"
SKILL_DIR = Path(__file__).parent
PROJECT_DIR = SKILL_DIR.parent.parent.parent  # .claude/skills/reporte-cuenta -> proyecto
LOGO_PATH = PROJECT_DIR / "assets" / "logo_tras_amarillo.jpg"
TEMPLATE_PATH = SKILL_DIR / "report_template.html"
OUT_DIR = PROJECT_DIR / "reports"

# Overrides por cuenta (ID sin 'act_'): nombre a mostrar y/o logo (archivo en assets/).
# Se aplican salvo que se pasen --name / --logo explicitos en la CLI. Sirve para
# cuentas sin 'name' en Meta o con marca propia (ej. Reloaded).
ACCOUNT_OVERRIDES = {
    "1372445320756173": {"name": "JMChef"},                       # sin nombre en Meta
    "3314437138860175": {"name": "CI Textil"},                    # sin nombre util en Meta
    "4100755920144855": {"logo": "logo_realoaded.png"},           # Carolina Varela -> Reloaded
}

MSG_ACTION = "onsite_conversion.messaging_conversation_started_7d"
MONTHS_ES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
             "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# Historico mensual hardcodeado por cuenta -> grafica de tendencia (barras=leads,
# linea=invertido). Los meses desde "trend_start" (inclusive) se traen de la API;
# el resto queda fijo con estos valores.
MONTHLY_HISTORY = {
    "4100755920144855": {  # Carolina Varela
        "trend_start": "2026-05",
        "rows": [
            {"label": "octubre-25",   "spend": 2695358, "leads": 442},
            {"label": "noviembre-25", "spend": 2235218, "leads": 658},
            {"label": "diciembre-25", "spend": 2855630, "leads": 708},
            {"label": "enero-26",     "spend": 2907845, "leads": 705},
            {"label": "febrero-26",   "spend": 1940957, "leads": 471},
            {"label": "marzo-26",     "spend": 3292218, "leads": 930},
            {"label": "abril-26",     "spend": 2105475, "leads": 465},
        ],
    },
}


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


def read_token() -> str:
    return read_env_var("META_TOKEN")


# ── helpers ─────────────────────────────────────────────────────────────────
def to_num(v):
    if v in (None, "", "Not available"):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def compact_money(n):
    """2081205 -> '$2,1M'; 3251.88 -> '$3,3k'; 850 -> '$850' (estilo es-AR)."""
    n = float(n or 0)
    a = abs(n)
    if a >= 1e6:
        v, suf = n / 1e6, "M"
    elif a >= 1e3:
        v, suf = n / 1e3, "k"
    else:
        return f"${round(n)}"
    s = f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"${s}{suf}"


def extract_action(insight, action_type):
    for a in insight.get("actions", []) or []:
        if a.get("action_type") == action_type:
            return to_num(a.get("value"))
    return 0.0


# Alias de tokens -> nombre legible (para la etiqueta descriptiva del anuncio).
# Sirve para separar nombres "pegados" (varias palabras en un solo token).
CAMP_ALIAS = {
    "DIAMADRE": "Día de la Madre",
    "PROMOSJULIO": "Promos Julio",
    "BARRAMOVIL": "Barra Móvil",
    "VIDEO60PLUS": "60plus",
    "EVENTOSJULIO": "Eventos Julio",
    "EVENTOSJUNIO": "Eventos Junio",
    "EVENTOSMAYO": "Eventos Mayo",
    "CHINAOESTE": "China Oeste",
    "GRASAREBELDE": "Grasa Rebelde",
    "HOTSALEJULIO": "Hot Sale Julio",
    "AGENDA2027": "Agenda 2027",
    "IMPRESIONDTF": "Impresión DTF",
    "INSTITUCIONAL2": "Institucional 2",
    "4PILARES": "4 Pilares",
}
# Tokens de ruido a descartar del nombre del anuncio
NOISE = {"ADSET", "NEW", "NUEVOVIDEO", "NUEVOVID", "NUEVO", "NUEVA", "COPY", "COPIA"}
# Token de periodo tipo MAR26, JUL26, ABR26 (3 letras + 2 digitos)
PERIOD_RE = re.compile(r"^[A-Z]{3}\d{2}$", re.I)
# Prefijos de estado al inicio del nombre del anuncio (se descartan)
AD_PREFIXES = ("inactivo -", "inactivo-", "activo -", "activo-", "pausado -", "pausado-")
# Tipo de anuncio detectado desde el nombre del anuncio
TYPE_ITEMS = [
    ("REEL", "Reel"), ("CARRUSEL", "Carrusel"), ("CAROUSEL", "Carrusel"),
    ("CARRU", "Carrusel"), ("PLACA", "Placa"), ("POST", "Placa"), ("IMAGEN", "Placa"),
    ("IMG", "Placa"), ("FOTO", "Placa"), ("VIDEO", "Video"),
]


def parse_type(ad_name):
    """REEL_DIAMADRE -> 'Reel'."""
    up = (ad_name or "").upper()
    for key, label in TYPE_ITEMS:
        if key in up:
            return label
    return ""


def parse_ad_desc(ad_name):
    """Nombre descriptivo del anuncio a partir de ad_name, sin el token de tipo.
    Descarta prefijos de estado ('Inactivo - '), el token de tipo (REEL/PLACA/...),
    ruido (NUEVO, COPY...), periodos (MAR26) y numeros sueltos.
    REEL_PREMIUM -> 'Premium'; PLACA_PREMIUM_BLANCA -> 'Premium Blanca';
    EVENTOS_REEL_BARRAMOVIL -> 'Eventos Barramovil'; PLACA_LOCALIZADA_MAR26 -> 'Localizada'."""
    s = (ad_name or "").strip()
    low = s.lower()
    for p in AD_PREFIXES:
        if low.startswith(p):
            s = s[len(p):].strip()
            break
    type_keys = {k for k, _ in TYPE_ITEMS}
    kept = []
    for t in [p for p in s.split("_") if p]:
        u = t.upper()
        if u in type_keys or u in NOISE or PERIOD_RE.match(u) or re.fullmatch(r"V?\d+", u):
            continue
        kept.append(t)
    if not kept:
        return parse_type(ad_name) or (s or "—")
    return " ".join(CAMP_ALIAS.get(t.upper(), t.capitalize()) for t in kept)


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
    """Sigue paginacion y devuelve la lista 'data' completa."""
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
        return ""  # la plantilla muestra placeholder si viene vacio


# ── build conclusiones (borrador editable) ──────────────────────────────────
def demo_sentence(metric_label, demo_gender, demo_age):
    ml = metric_label.lower()
    parts = []
    if demo_gender:
        tot = sum(d["metric"] for d in demo_gender)
        top = max(demo_gender, key=lambda d: d["metric"])
        if tot > 0 and top["metric"] > 0:
            parts.append(f"predominaron {top['label'].lower()} "
                         f"({top['metric'] / tot * 100:.0f}% de los {ml})")
    if demo_age:
        topa = max(demo_age, key=lambda d: d["metric"])
        if topa["metric"] > 0:
            parts.append(f"el grupo etario de mayor volumen fue {topa['label']}")
    return ("En cuanto al público, " + " y ".join(parts) + ".") if parts else ""


def draft_conclusions(account_name, month_label, total_spend, total_metric,
                      cost_per, ctr, metric_label, cost_label, daily, ads,
                      demo_gender=None, demo_age=None):
    ml = metric_label.lower()
    cl = cost_label.lower()
    spend_s = compact_money(total_spend)
    cost_s = compact_money(cost_per) if cost_per else "—"
    lines = [
        f"Durante {month_label}, {account_name} invirtio {spend_s} en Meta Ads, "
        f"generando {total_metric:,.0f} {ml} a un {cl} promedio de {cost_s}."
    ]
    if daily:
        peak = max(daily, key=lambda d: d["metric"])
        if peak["metric"] > 0:
            d = date.fromisoformat(peak["date"])
            lines.append(
                f"El dia de mayor volumen fue el {d.day}/{d.month}, "
                f"con {peak['metric']:,.0f} {ml}."
            )
    best = max(ads, key=lambda a: a["metric"]) if ads else None
    if best and best["metric"] > 0:
        lines.append(
            f"El anuncio con mas {ml} fue <strong>{best['campaign_label']}</strong> "
            f"({best['metric']:,.0f} {ml})."
        )
    # Conclusion segun CTR
    if ctr >= 2.0:
        interp = "un nivel muy bueno que refleja anuncios relevantes para la audiencia"
    elif ctr >= 1.0:
        interp = "un nivel saludable"
    else:
        interp = "un nivel bajo; conviene revisar creativos y segmentacion"
    ctr_line = f"El CTR promedio del mes fue {ctr:.2f}%, {interp}."
    ctr_ads = [a for a in ads if a["spend"] > 0 and a["ctr"] > 0]
    if ctr_ads:
        best_ctr = max(ctr_ads, key=lambda a: a["ctr"])
        ctr_line += (f" El anuncio con mejor CTR fue <strong>{best_ctr['campaign_label']}</strong> "
                     f"({best_ctr['ctr']:.2f}%).")
    lines.append(ctr_line)
    ds = demo_sentence(metric_label, demo_gender or [], demo_age or [])
    if ds:
        lines.append(ds)
    return " ".join(lines)


# ── seguidores de Instagram (token aparte; snapshot mensual persistido) ───────
IG_STORE = PROJECT_DIR / "data" / "ig_followers.json"
# Semilla manual de historico de seguidores (Meta no expone el total historico).
# Formato: {ig_user_id: {"YYYY-MM": seguidores}}.
IG_SEED = {
    "17841475093486297": {  # FINTAX @fintaxsolucionesintegrales
        "2025-11": 593,
        "2025-12": 612,
        "2026-01": 634,
        "2026-02": 656,
        "2026-03": 729,
        "2026-04": 880,
    },
}


def ig_get(path, params, token):
    params = dict(params, access_token=token)
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    for _ in range(3):  # reintenta ante hipos transitorios de la API
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
    return {}


def resolve_ig(acct_id, token):
    """Cuenta publicitaria -> pagina promocionada -> cuenta de IG vinculada."""
    pp = ig_get(f"act_{acct_id}/promote_pages", {"fields": "id,name", "limit": 5}, token)
    for pg in pp.get("data", []):
        info = ig_get(pg["id"], {"fields": "instagram_business_account{id,username,followers_count}"}, token)
        ig = info.get("instagram_business_account")
        if ig and ig.get("id"):
            return {"id": ig["id"], "username": ig.get("username", ""),
                    "followers": int(to_num(ig.get("followers_count")))}
    return None


def _load_ig_store():
    if IG_STORE.exists():
        try:
            return json.loads(IG_STORE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_ig_store(store):
    IG_STORE.parent.mkdir(exist_ok=True)
    IG_STORE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def build_followers(acct_id, year, month, ig_token):
    """Resuelve seguidores de IG en vivo y persiste el snapshot del mes. Si el token
    no esta disponible/valido, cae a los datos ya guardados (indice _accounts) para
    no perder la seccion."""
    store = _load_ig_store()
    month_key = f"{year:04d}-{month:02d}"
    ig = resolve_ig(acct_id, ig_token) if ig_token else None

    if ig:
        ig_id, username, current = ig["id"], ig["username"], ig["followers"]
        store.setdefault("_accounts", {})[acct_id] = ig_id
        store.setdefault("_usernames", {})[ig_id] = username
        store.setdefault(ig_id, {}).setdefault(month_key, current)  # no pisa snapshots
        _save_ig_store(store)
    else:
        ig_id = store.get("_accounts", {}).get(acct_id)
        if not ig_id:
            print("    (sin Instagram vinculado)")
            return None
        username = store.get("_usernames", {}).get(ig_id, "")
        saved = {**IG_SEED.get(ig_id, {}), **store.get(ig_id, {})}
        current = saved.get(month_key) or (saved[max(saved)] if saved else 0)
        print("    (token IG no disponible; uso datos guardados)")

    merged = {**IG_SEED.get(ig_id, {}), **store.get(ig_id, {})}
    if not merged:
        return None
    trend = []
    for mk in sorted(merged):
        yy, mm = (int(x) for x in mk.split("-"))
        trend.append({"label": f"{MONTHS_ES[mm].lower()}-{yy % 100:02d}", "count": merged[mk]})
    prev_keys = [mk for mk in merged if mk < month_key]
    gain = (current - merged[max(prev_keys)]) if prev_keys else None
    print(f"    @{username} · {current:,} seguidores"
          + (f" ({gain:+,} en el mes)" if gain is not None else ""))
    return {"username": username, "current": current, "gain": gain, "trend": trend}


# ── tendencia mensual (historico hardcodeado + meses recientes desde API) ─────
def build_trend(acct_id, year, month, metric_of, token):
    hist = MONTHLY_HISTORY.get(acct_id)
    if not hist:
        return None
    rows = [dict(r) for r in hist["rows"]]
    y, m = (int(x) for x in hist["trend_start"].split("-"))
    while (y, m) <= (year, month):
        last = calendar.monthrange(y, m)[1]
        tr = json.dumps({"since": f"{y:04d}-{m:02d}-01", "until": f"{y:04d}-{m:02d}-{last:02d}"})
        agg = api_get_all(
            f"act_{acct_id}/insights",
            {"level": "account", "time_range": tr, "fields": "spend,clicks,actions", "limit": 200},
            token,
        )
        rows.append({
            "label": f"{MONTHS_ES[m].lower()}-{y % 100:02d}",
            "spend": sum(to_num(r.get("spend")) for r in agg),
            "leads": sum(metric_of(r) for r in agg),
        })
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return rows


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="ID de cuenta (sin 'act_')")
    ap.add_argument("--month", default=None, help="Mes YYYY-MM, ej 2026-05 (alternativa a --since/--until)")
    ap.add_argument("--since", default=None, help="Fecha inicio YYYY-MM-DD (requiere --until)")
    ap.add_argument("--until", default=None, help="Fecha fin YYYY-MM-DD (requiere --since)")
    ap.add_argument("--campaigns", default=None,
                    help="IDs de campana separados por coma; filtra el informe a esas campanas")
    ap.add_argument("--label", default=None,
                    help="Texto de periodo a mostrar (ej 'Cyber 2026'); default = mes o rango")
    ap.add_argument("--type", required=True, choices=["mensajes", "trafico"])
    ap.add_argument("--name", default=None,
                    help="Nombre a mostrar (si la cuenta no tiene name configurado)")
    ap.add_argument("--logo", default=None,
                    help="Archivo de logo (en assets/) para el encabezado; default logo_tras_amarillo.jpg")
    ap.add_argument("--no-demo", action="store_true",
                    help="Oculta la seccion 'Demografia de resultados' en el informe")
    args = ap.parse_args()
    if not args.month and not (args.since and args.until):
        sys.exit("ERROR: indica --month YYYY-MM o bien --since y --until")

    token = read_token()
    acct_id = args.account.replace("act_", "")
    if args.since and args.until:
        since, until = args.since, args.until
        y0, m0, _ = (int(x) for x in since.split("-"))
        y1, m1, _ = (int(x) for x in until.split("-"))
        year, month = y1, m1  # mes de referencia (para nombre de archivo)
        month_label = args.label or f"{MONTHS_ES[m0]}–{MONTHS_ES[m1]} {y1}"
    else:
        year, month = (int(x) for x in args.month.split("-"))
        last_day = calendar.monthrange(year, month)[1]
        since = f"{year:04d}-{month:02d}-01"
        until = f"{year:04d}-{month:02d}-{last_day:02d}"
        month_label = args.label or f"{MONTHS_ES[month]} {year}"
    time_range = json.dumps({"since": since, "until": until})

    # Filtro por campanas: aplica a serie diaria, nivel anuncio, miniaturas y demografia.
    campaign_mode = bool(args.campaigns)
    cfilter = {}
    if campaign_mode:
        ids = [c.strip() for c in args.campaigns.split(",") if c.strip()]
        cfilter["filtering"] = json.dumps(
            [{"field": "campaign.id", "operator": "IN", "value": ids}])

    is_msg = args.type == "mensajes"
    metric_label = "Mensajes" if is_msg else "Clics"
    cost_label = "Costo por mensaje" if is_msg else "Costo por clic"

    print(f"  Cuenta act_{acct_id} · {month_label} · tipo {args.type}")

    # 1) Cuenta
    override = ACCOUNT_OVERRIDES.get(acct_id, {})
    acct = api_get(f"act_{acct_id}", {"fields": "name,currency"}, token)
    account_name = args.name or override.get("name") or acct.get("name") or f"act_{acct_id}"
    currency = acct.get("currency", "ARS")

    def metric_of(ins):
        return extract_action(ins, MSG_ACTION) if is_msg else to_num(ins.get("clicks"))

    # 2) Serie diaria
    print("  Trayendo serie diaria…")
    daily_raw = api_get_all(
        f"act_{acct_id}/insights",
        {"level": "account", "time_increment": 1, "time_range": time_range,
         "fields": "spend,impressions,clicks,ctr,actions", "limit": 500, **cfilter},
        token,
    )
    daily = [{"date": r.get("date_start"),
              "spend": to_num(r.get("spend")),
              "metric": metric_of(r)} for r in daily_raw]
    daily.sort(key=lambda d: d["date"])

    # 3) Nivel anuncio
    print("  Trayendo nivel anuncio…")
    ads_raw = api_get_all(
        f"act_{acct_id}/insights",
        {"level": "ad", "time_range": time_range,
         "fields": "ad_id,ad_name,spend,impressions,clicks,ctr,actions",
         "limit": 500, **cfilter},
        token,
    )

    # 4) Miniaturas (ad_id -> thumbnail_url)
    print("  Trayendo miniaturas…")
    thumbs = {}
    for ad in api_get_all(
        f"act_{acct_id}/ads",
        {"fields": "id,creative{thumbnail_url}", "time_range": time_range,
         "thumbnail_width": 160, "thumbnail_height": 160, "limit": 500, **cfilter},
        token,
    ):
        url = (ad.get("creative") or {}).get("thumbnail_url")
        if url:
            thumbs[ad["id"]] = url

    ads = []
    for r in ads_raw:
        spend = to_num(r.get("spend"))
        metric = metric_of(r)
        messages = extract_action(r, MSG_ACTION)
        clicks = to_num(r.get("clicks"))
        ads.append({
            "thumb": "",  # se rellena abajo solo para los visibles
            "thumb_url": thumbs.get(r.get("ad_id"), ""),
            # etiqueta principal = nombre descriptivo del anuncio (desde ad_name)
            "campaign_label": parse_ad_desc(r.get("ad_name", "")),
            "ad_type": parse_type(r.get("ad_name", "")),
            "spend": spend,
            "metric": metric,
            "cost": (spend / metric) if metric else 0.0,
            "messages": messages,
            "cost_msg": (spend / messages) if messages else 0.0,
            "clicks": clicks,
            "cost_click": (spend / clicks) if clicks else 0.0,
            "ctr": to_num(r.get("ctr")),
        })
    ads.sort(key=lambda a: a["spend"], reverse=True)

    # Embeber miniaturas (descarga unica por url)
    cache = {}
    for a in ads:
        u = a["thumb_url"]
        if u:
            if u not in cache:
                cache[u] = fetch_thumb(u)
            a["thumb"] = cache[u]
        del a["thumb_url"]

    # Totales
    total_spend = sum(d["spend"] for d in daily)
    total_metric = sum(d["metric"] for d in daily)
    total_impr = sum(to_num(r.get("impressions")) for r in daily_raw)
    total_clicks = sum(to_num(r.get("clicks")) for r in daily_raw)
    cost_per = (total_spend / total_metric) if total_metric else 0.0
    ctr = (total_clicks / total_impr * 100) if total_impr else 0.0

    # Demografia (genero + edad) con la metrica principal — excluye "sin dato"
    print("  Trayendo demografia…")

    def demo_rows(bd):
        return api_get_all(
            f"act_{acct_id}/insights",
            {"level": "account", "time_range": time_range, "breakdowns": bd,
             "fields": "spend,clicks,actions", "limit": 200, **cfilter},
            token,
        )

    GENDER_LABELS = {"female": "Mujeres", "male": "Hombres"}
    gmap = {r.get("gender"): metric_of(r) for r in demo_rows("gender")}
    demo_gender = [{"label": GENDER_LABELS[g], "metric": gmap[g]}
                   for g in ("female", "male") if g in gmap]

    AGE_ORDER = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
    amap = {r.get("age"): metric_of(r) for r in demo_rows("age")}
    demo_age = [{"label": a, "metric": amap[a]} for a in AGE_ORDER if a in amap]

    conclusions = draft_conclusions(
        account_name, month_label, total_spend, total_metric,
        cost_per, ctr, metric_label, cost_label, daily, ads,
        None if args.no_demo else demo_gender,
        None if args.no_demo else demo_age)

    # Tendencia mensual y seguidores son metricas mensuales de cuenta: no aplican a
    # un informe filtrado por campanas.
    if campaign_mode:
        trend = followers = None
    else:
        trend = build_trend(acct_id, year, month, metric_of, token)
        if trend:
            print("  Tendencia mensual lista.")
        print("  Resolviendo Instagram…")
        followers = build_followers(acct_id, year, month, token)

    # Logo (--logo > override por cuenta > default trasmedia)
    logo_file = args.logo or override.get("logo")
    logo_path = LOGO_PATH
    if logo_file:
        cand = Path(logo_file)
        logo_path = cand if cand.is_absolute() else (PROJECT_DIR / "assets" / logo_file)
    logo = ""
    if logo_path.exists():
        mime = mimetypes.guess_type(str(logo_path))[0] or "image/jpeg"
        logo = b64_data_uri(logo_path.read_bytes(), mime)
    else:
        print(f"  AVISO: logo no encontrado en {logo_path}")

    data = {
        "account_name": account_name,
        "currency": currency,
        "month_label": month_label,
        "report_type": args.type,
        "metric_label": metric_label,
        "cost_label": cost_label,
        "logo": logo,
        "conclusions": conclusions,
        "kpis": {"spend": total_spend, "metric": total_metric,
                 "cost_per": cost_per, "ctr": ctr},
        "daily": daily,
        "show_demo": not args.no_demo,
        "demo": {"gender": demo_gender, "age": demo_age},
        "ads": ads,
        "trend": trend,
        "followers": followers,
    }

    # Render
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = template.replace("/*__DATA__*/{}",
                            json.dumps(data, ensure_ascii=False))

    OUT_DIR.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in account_name).strip("_")[:40]
    if campaign_mode:
        tag = "".join(c if c.isalnum() else "_" for c in month_label).strip("_")[:30]
        out = OUT_DIR / f"reporte_{safe}_{tag}_{args.type}.html"
    else:
        out = OUT_DIR / f"reporte_{safe}_{year:04d}-{month:02d}_{args.type}.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n  OK -> {out}")


if __name__ == "__main__":
    main()
