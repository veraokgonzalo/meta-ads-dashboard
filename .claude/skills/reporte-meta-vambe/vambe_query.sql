-- Export de Vambe para el reporte Meta + Vambe. Correr con la tool MCP `query-analytics`
-- (limit 200) pasando en `params`, en hora de Chile:
--   {"since": "<YYYY-MM-DD> 00:00:00", "until_excl": "<dia siguiente a --until> 00:00:00"}
-- Ej. agosto 2026: {"since": "2026-08-01 00:00:00", "until_excl": "2026-09-01 00:00:00"}
--
-- Una fila por (semana, anuncio). Personas = ai_customer (une WhatsApp e IG), atribuidas a su
-- PRIMER anuncio del periodo. week_idx = bloques de 7 dias desde `since` (0 = dias 1-7).
-- booked = tiene hora creada despues de ese primer mensaje; cada persona cae en un solo estado
-- por su mejor resultado: asistio > no asistio > pendiente > cancelo.
WITH ev AS (
  SELECT c.ai_customer_id AS cust, u.created_at AS ts, u.ad_id AS ad_id
  FROM v_utm_events u INNER JOIN v_ai_contact c ON c.id = u.ai_contact_id
  WHERE u.ad_id != ''
    AND u.created_at >= toDateTime({since:String}, 'America/Santiago')
    AND u.created_at < toDateTime({until_excl:String}, 'America/Santiago')
),
cust AS (SELECT cust, min(ts) AS first_ad_at, argMin(ad_id, ts) AS ad_id FROM ev GROUP BY cust),
ap AS (
  SELECT cust.cust AS k, max(ap.status='ATTENDED') AS a_att, max(ap.status='NO_SHOW') AS a_ns, max(ap.status IN ('SCHEDULED','CONFIRMED')) AS a_pend
  FROM v_appointments ap INNER JOIN cust ON ap.ai_customer_id = cust.cust
  WHERE ap.created_at >= cust.first_ad_at
  GROUP BY cust.cust
)
SELECT
  intDiv(dateDiff('day', toDate(toDateTime({since:String}, 'America/Santiago'), 'America/Santiago'), toDate(cust.first_ad_at, 'America/Santiago')), 7) AS week_idx,
  cust.ad_id AS ad_id,
  count() AS arrived,
  countIf(ap.k = cust.cust) AS booked,
  countIf(ap.k = cust.cust AND ap.a_att = 1) AS attended,
  countIf(ap.k = cust.cust AND ap.a_att = 0 AND ap.a_ns = 1) AS no_show,
  countIf(ap.k = cust.cust AND ap.a_att = 0 AND ap.a_ns = 0 AND ap.a_pend = 1) AS pending,
  countIf(ap.k = cust.cust AND ap.a_att = 0 AND ap.a_ns = 0 AND ap.a_pend = 0) AS cancelled
FROM cust LEFT JOIN ap ON ap.k = cust.cust
GROUP BY week_idx, ad_id
ORDER BY week_idx, ad_id
