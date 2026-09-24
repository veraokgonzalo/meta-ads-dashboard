#!/usr/bin/env python3
"""
Informe combinado Meta Ads + Vambe (mensajes -> agendas -> asistencias).

Meta se consulta en vivo (Graph API, META_TOKEN del .env). Vambe NO tiene API
accesible desde aca: sus datos se exportan antes via MCP (query-analytics, ver
vambe_query.sql) a un JSON en data/ con filas por (semana, anuncio). Ver SKILL.md.

Uso:
  python generate_vambe_report.py --account 4100755920144855 --month 2026-08 \
      --vambe data/vambe_carolina_varela_2026-08.json
  python generate_vambe_report.py --account 4100755920144855 --since 2026-09-01 --until 2026-09-15 \
      --vambe data/vambe_carolina_varela_2026-09-01_2026-09-15.json
"""
import argparse
import calendar
import json
import mimetypes
import sys
from datetime import date, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).parent
# Helpers compartidos con el reporte de Meta (API, miniaturas, nombres de anuncios, overrides)
sys.path.insert(0, str(SKILL_DIR.parent / "reporte-cuenta"))
from generate_report import (  # noqa: E402
    ACCOUNT_OVERRIDES, CAMP_ALIAS, LOGO_PATH, MONTHS_ES, MSG_ACTION, OUT_DIR, PROJECT_DIR,
    api_get, api_get_all, b64_data_uri, compact_money, extract_action, fetch_thumb,
    parse_ad_desc, parse_type, read_token, to_num,
)

TEMPLATE_PATH = SKILL_DIR / "report_template.html"
COUNTS = ("arrived", "booked", "attended", "no_show", "pending", "cancelled")
CHANNEL_TOKENS = {"WAPP", "WSP", "WHATSAPP", "WA"}


def mon3(d):
    return MONTHS_ES[d.month][:3].lower()


def week_blocks(since, until):
    """Bloques de 7 dias desde `since` (el ultimo puede ser mas corto), con etiqueta:
    '1–7 ago', '29 ago–4 sep', '31 ago'."""
    blocks, d0 = [], since
    while d0 <= until:
        d1 = min(d0 + timedelta(days=6), until)
        if d0 == d1:
            label = f"{d0.day} {mon3(d0)}"
        elif d0.month == d1.month:
            label = f"{d0.day}–{d1.day} {mon3(d1)}"
        else:
            label = f"{d0.day} {mon3(d0)}–{d1.day} {mon3(d1)}"
        blocks.append(label)
        d0 = d1 + timedelta(days=1)
    return blocks


def range_label(since, until):
    """'1 al 15 de septiembre de 2026' / '25 de agosto al 7 de septiembre de 2026'."""
    m0, m1 = MONTHS_ES[since.month].lower(), MONTHS_ES[until.month].lower()
    if since.year != until.year:
        return f"{since.day} de {m0} de {since.year} al {until.day} de {m1} de {until.year}"
    if since.month != until.month:
        return f"{since.day} de {m0} al {until.day} de {m1} de {until.year}"
    return f"{since.day} al {until.day} de {m1} de {until.year}"


def div(a, b):
    return (a / b) if b else None


def campaign_label(name):
    """PREMIUM_WAPP -> 'Premium'; 60PLUS_WAPP -> '60plus' (sin sufijo de canal)."""
    toks = [t for t in (name or "").split("_") if t and t.upper() not in CHANNEL_TOKENS]
    return " ".join(CAMP_ALIAS.get(t.upper(), t.capitalize()) for t in toks) or name or "—"


def with_rates(row):
    """Agrega CTR, costos y tasa de asistencia a una fila de anuncio o campana."""
    spend = row["spend"]
    row["ctr"] = (row["clicks"] / row["impressions"] * 100) if row["impressions"] else None
    row["cost_booked"] = div(spend, row["booked"]) if spend else None
    row["cost_attended"] = div(spend, row["attended"]) if spend else None
    row["show_rate"] = div(row["attended"], row["booked"])
    return row


def draft_conclusions(name, intro, k, ads):
    lines = [
        f"{intro}, {name} invirtió {compact_money(k['spend'])} en Meta Ads y "
        f"generó {k['messages']:,.0f} mensajes. De ellos, {k['arrived']:,} personas llegaron a "
        f"Vambe desde un anuncio, {k['booked']:,} agendaron hora y {k['attended']:,} asistieron: "
        f"un costo de {compact_money(k['cost_booked'])} por agenda y "
        f"{compact_money(k['cost_attended'])} por asistencia."
    ]
    lines.append(
        f"El {k['booked_rate'] * 100:.0f}% de quienes llegaron agendó y el "
        f"{k['show_rate'] * 100:.0f}% de quienes agendaron asistió."
    )
    ranked = [a for a in ads if a["attended"] and a["spend"]]
    if ranked:
        best = min(ranked, key=lambda a: a["cost_attended"])
        lines.append(
            f"El anuncio más eficiente fue <strong>{best['label']}</strong>, con "
            f"{best['attended']} asistencias a {compact_money(best['cost_attended'])} cada una."
        )
    s = k["status"]
    if s["cancelled"]:
        lines.append(
            f"De quienes agendaron, {s['cancelled']} cancelaron sin reagendar: es la principal "
            f"fuga después de la agenda."
        )
    return " ".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="ID de cuenta (sin 'act_')")
    ap.add_argument("--month", default=None, help="Mes YYYY-MM (alternativa a --since/--until)")
    ap.add_argument("--since", default=None, help="Fecha inicio YYYY-MM-DD (requiere --until)")
    ap.add_argument("--until", default=None, help="Fecha fin YYYY-MM-DD, inclusive (requiere --since)")
    ap.add_argument("--label", default=None, help="Texto de periodo a mostrar (ej 'Cyber 2026')")
    ap.add_argument("--vambe", required=True, help="JSON exportado de Vambe (ver SKILL.md)")
    ap.add_argument("--name", default=None)
    ap.add_argument("--logo", default=None)
    args = ap.parse_args()

    # Periodo: mes completo o rango libre
    if args.month:
        y, m = (int(x) for x in args.month.split("-"))
        since, until = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
        period_label = args.label or f"{MONTHS_ES[m]} {y}"
        intro = f"Durante {period_label.lower()}" if not args.label else f"En {period_label}"
        noun, file_tag = "mes", f"{y:04d}-{m:02d}"
    elif args.since and args.until:
        since, until = date.fromisoformat(args.since), date.fromisoformat(args.until)
        if until < since:
            sys.exit("ERROR: --until es anterior a --since")
        rl = range_label(since, until)
        period_label = args.label or rl[0].upper() + rl[1:]
        intro = f"En {period_label}" if args.label else f"Del {rl}"
        noun, file_tag = "período", f"{since.isoformat()}_{until.isoformat()}"
    else:
        sys.exit("ERROR: indica --month YYYY-MM o bien --since y --until")

    token = read_token()
    acct_id = args.account.replace("act_", "")
    time_range = json.dumps({"since": since.isoformat(), "until": until.isoformat()})

    vambe_path = Path(args.vambe)
    if not vambe_path.is_absolute():
        vambe_path = PROJECT_DIR / vambe_path
    vambe = json.loads(vambe_path.read_text(encoding="utf-8"))
    if (vambe.get("since"), vambe.get("until")) != (since.isoformat(), until.isoformat()):
        sys.exit(f"ERROR: el JSON de Vambe cubre {vambe.get('since')} a {vambe.get('until')}, "
                 f"no {since} a {until}. Reexportar con esas fechas.")
    vrows = vambe["rows"]

    print(f"  Cuenta act_{acct_id} · {period_label} · Meta + Vambe")
    override = ACCOUNT_OVERRIDES.get(acct_id, {})
    acct = api_get(f"act_{acct_id}", {"fields": "name,currency"}, token)
    name = args.name or override.get("name") or acct.get("name") or f"act_{acct_id}"

    # Meta: serie diaria -> semanas (bloques de 7 dias desde `since`, igual que la consulta de Vambe)
    print("  Trayendo serie diaria…")
    daily_raw = api_get_all(
        f"act_{acct_id}/insights",
        {"level": "account", "time_increment": 1, "time_range": time_range,
         "fields": "spend,impressions,clicks,actions", "limit": 500},
        token,
    )
    weeks = [{"label": lbl, "spend": 0.0, "messages": 0.0, **{c: 0 for c in COUNTS}}
             for lbl in week_blocks(since, until)]
    for r in daily_raw:
        w = weeks[(date.fromisoformat(r["date_start"]) - since).days // 7]
        w["spend"] += to_num(r.get("spend"))
        w["messages"] += extract_action(r, MSG_ACTION)
    for r in vrows:
        for c in COUNTS:
            weeks[r["week_idx"]][c] += r[c]
    for w in weeks:
        w["cost_booked"] = div(w["spend"], w["booked"])
        w["cost_attended"] = div(w["spend"], w["attended"])

    # Meta: nivel anuncio + miniaturas
    print("  Trayendo nivel anuncio…")
    ads_raw = api_get_all(
        f"act_{acct_id}/insights",
        {"level": "ad", "time_range": time_range,
         "fields": "campaign_id,campaign_name,ad_id,ad_name,spend,impressions,clicks,actions",
         "limit": 500},
        token,
    )
    print("  Trayendo miniaturas…")
    thumbs = {}
    for ad in api_get_all(
        f"act_{acct_id}/ads",
        {"fields": "id,creative{thumbnail_url}", "time_range": time_range,
         "thumbnail_width": 160, "thumbnail_height": 160, "limit": 500},
        token,
    ):
        url = (ad.get("creative") or {}).get("thumbnail_url")
        if url:
            thumbs[ad["id"]] = url

    by_ad = {}
    for r in vrows:
        acc = by_ad.setdefault(r["ad_id"], {c: 0 for c in COUNTS})
        for c in COUNTS:
            acc[c] += r[c]

    ads, cache, camps = [], {}, {}
    for r in ads_raw:
        ad_id = r.get("ad_id")
        v = by_ad.pop(ad_id, {c: 0 for c in COUNTS})
        url = thumbs.get(ad_id, "")
        if url and url not in cache:
            cache[url] = fetch_thumb(url)
        base = {"spend": to_num(r.get("spend")), "messages": extract_action(r, MSG_ACTION),
                "clicks": to_num(r.get("clicks")), "impressions": to_num(r.get("impressions")), **v}
        cid = r.get("campaign_id")
        camp = camps.setdefault(cid, {"id": cid, "label": campaign_label(r.get("campaign_name", "")),
                                      "n_ads": 0, **{f: 0 for f in base}})
        camp["n_ads"] += 1
        for f in base:
            camp[f] += base[f]
        ads.append(with_rates({
            "thumb": cache.get(url, ""),
            "label": parse_ad_desc(r.get("ad_name", "")),
            "ad_type": parse_type(r.get("ad_name", "")),
            "campaign_id": cid,
            "campaign_label": camp["label"],
            **base,
        }))
    ads.sort(key=lambda a: a["spend"], reverse=True)

    # Color de cada campana = posicion por inversion (la plantilla tiene la paleta)
    campaigns = sorted((with_rates(c) for c in camps.values()), key=lambda c: c["spend"], reverse=True)
    color_of = {c["id"]: i for i, c in enumerate(campaigns)}
    for c in campaigns:
        c["color"] = color_of[c["id"]]
    for a in ads:
        a["color"] = color_of[a["campaign_id"]]

    # Personas que llegaron por anuncios sin inversion en el periodo (anuncios viejos/apagados)
    orphan = {c: sum(v[c] for v in by_ad.values()) for c in COUNTS}
    if orphan["arrived"]:
        row = with_rates({"thumb": "", "label": f"Anuncios sin inversión en el {noun}",
                          "ad_type": f"{len(by_ad)} anuncios", "spend": 0.0, "messages": 0.0,
                          "clicks": 0, "impressions": 0, **orphan, "color": None, "pinned": True})
        ads.append(row)
        campaigns.append({**row, "n_ads": len(by_ad)})

    # Totales
    spend = sum(w["spend"] for w in weeks)
    k = {
        "spend": spend,
        "messages": sum(w["messages"] for w in weeks),
        **{c: sum(w[c] for w in weeks) for c in COUNTS},
    }
    k["cost_booked"] = div(spend, k["booked"])
    k["cost_attended"] = div(spend, k["attended"])
    k["booked_rate"] = div(k["booked"], k["arrived"]) or 0
    k["show_rate"] = div(k["attended"], k["booked"]) or 0
    k["status"] = {c: k[c] for c in ("attended", "cancelled", "no_show", "pending")}

    logo_file = args.logo or override.get("logo")
    logo_path = (PROJECT_DIR / "assets" / logo_file) if logo_file else LOGO_PATH
    logo = ""
    if logo_path.exists():
        mime = mimetypes.guess_type(str(logo_path))[0] or "image/jpeg"
        logo = b64_data_uri(logo_path.read_bytes(), mime)

    data = {
        "account_name": name,
        "currency": acct.get("currency", "CLP"),
        "period_label": period_label,
        "period_noun": noun,
        "logo": logo,
        "conclusions": draft_conclusions(name, intro, k, ads),
        "kpis": k,
        "weeks": weeks,
        "ads": ads,
        "campaigns": campaigns,
        "vambe_extracted_at": vambe.get("extracted_at", ""),
    }

    html = TEMPLATE_PATH.read_text(encoding="utf-8").replace(
        "/*__DATA__*/{}", json.dumps(data, ensure_ascii=False))
    out_dir = OUT_DIR / f"{until.year:04d}-{until.month:02d}" / "html"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in name).strip("_")[:40]
    out = out_dir / f"reporte_{safe}_{file_tag}_meta_vambe.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n  Meta: {k['messages']:,.0f} mensajes · Vambe: {k['arrived']} llegaron, "
          f"{k['booked']} agendaron, {k['attended']} asistieron")
    print(f"  OK -> {out}")


if __name__ == "__main__":
    main()
