---
name: reporte-meta-vambe
description: Genera un informe HTML que cruza Meta Ads con Vambe (mensajes de anuncios -> personas que agendaron hora -> asistencias), con costo por agenda y por asistencia, evolucion semanal, estado de los agendados y vistas por campana y por anuncio. Usar cuando el usuario pida un "reporte Meta + Vambe", "reporte de agendas/asistencias", "cuantos mensajes agendaron/asistieron", "costo por agenda" o "costo por asistencia" para una cuenta, por mes o por un periodo de fechas.
---

# Reporte Meta + Vambe — mensajes → agendas → asistencias

Genera un `.html` autocontenido (mismo diseño que el reporte de cuenta de Meta) que combina:
- **Meta Ads** (en vivo, Graph API con `META_TOKEN` del `.env`): inversion, mensajes, CTR,
  por dia, por campana y por anuncio.
- **Vambe** (exportado antes via MCP): personas que llegaron desde un anuncio, cuantas
  agendaron hora en el sistema de reservas y en que termino cada agenda.

Hoy aplica a **Carolina Varela** (cuenta `4100755920144855`, WhatsApp atendido por Vambe).

## Parametros
Preguntar si falta:
- **account**: ID de la cuenta de Meta (sin `act_`).
- **periodo**, una de dos:
  - `--month YYYY-MM` → mes completo.
  - `--since YYYY-MM-DD --until YYYY-MM-DD` → rango libre (ambas fechas inclusive).
  - Opcional `--label "Cyber 2026"`: texto de periodo en el encabezado (reemplaza al rango).

## Paso 1 — Exportar Vambe (MCP)
Vambe no tiene API accesible desde el script, asi que primero se consulta por MCP.

1. Leer `vambe_query.sql` (en esta carpeta) y correrlo con la tool MCP `query-analytics`
   del servidor `vambe` (si no se uso antes en la sesion, llamar primero a `read_skill`
   con `query-analytics`). Pasar `limit: 200` y en `params` las fechas en hora de Chile:
   - `since`: `"<since> 00:00:00"`
   - `until_excl`: `"<dia siguiente a until> 00:00:00"`
   Ej. agosto 2026 → `{"since": "2026-08-01 00:00:00", "until_excl": "2026-09-01 00:00:00"}`.
   La zona `America/Santiago` resuelve sola el horario de verano.
   Hay ~10 filas por semana, asi que 200 filas alcanzan para ~4 meses. Si la respuesta dice
   `truncated: true`, avisar al usuario y usar un periodo mas corto (no generar con datos
   incompletos).
2. Guardar el resultado en `data/vambe_<cuenta>_<periodo>.json` (ej.
   `data/vambe_carolina_varela_2026-08.json` o `..._2026-09-01_2026-09-15.json`):

```json
{
 "account_id": "4100755920144855",
 "since": "2026-08-01",
 "until": "2026-08-31",
 "timezone": "America/Santiago",
 "extracted_at": "<fecha de hoy YYYY-MM-DD>",
 "query": ".claude/skills/reporte-meta-vambe/vambe_query.sql",
 "rows": [ { "week_idx": 0, "ad_id": "…", "arrived": 1, "booked": 0, "attended": 0,
             "no_show": 0, "pending": 0, "cancelled": 0 }, … ]
}
```

   `since`/`until` deben ser exactamente los del reporte: el script lo valida.
   Verificar que la suma de `arrived` sea razonable frente a los mensajes de Meta (en
   agosto 2026: 845 personas vs 923 mensajes).

## Paso 2 — Generar el HTML

```
python .claude/skills/reporte-meta-vambe/generate_vambe_report.py --account 4100755920144855 \
  --month 2026-08 --vambe data/vambe_carolina_varela_2026-08.json

python .claude/skills/reporte-meta-vambe/generate_vambe_report.py --account 4100755920144855 \
  --since 2026-09-01 --until 2026-09-15 --vambe data/vambe_carolina_varela_2026-09-01_2026-09-15.json
```

Opcionales: `--label`, `--name`, `--logo` (igual que en `reporte-cuenta`; los overrides por
cuenta de nombre/logo se toman de `ACCOUNT_OVERRIDES` en `reporte-cuenta/generate_report.py`).

Sale en `reports/<YYYY-MM del until>/html/`:
- mes: `reporte_<cuenta>_<YYYY-MM>_meta_vambe.html`
- rango: `reporte_<cuenta>_<since>_<until>_meta_vambe.html`

Despues de generar: abrir el HTML y revisar/editar el **resumen** (es un borrador
automatico). Para PDF usar el mismo conversor del otro reporte:
`python .claude/skills/reporte-cuenta/html_to_pdf.py <input.html> [output.pdf]`
(en el PDF la tabla queda en el orden inicial, por inversion).

## Componentes del informe
Encabezado · Resumen · 6 KPIs (Inversion, Mensajes, Agendados, Asistencias, Costo por agenda,
Costo por asistencia; cada uno marca si viene de Meta o Vambe) · Evolucion semanal (barras de
mensajes/agendas/asistencias + lineas de costo por agenda y por asistencia) · Que paso con los
que agendaron (embudo mensaje → Vambe → agenda → asistencia y estado de los agendados) ·
Detalle por campana · Detalle por anuncio · Referencia.

- Tablas por campana y por anuncio: inversion, mensajes, CTR, agendas, asistencias, tasa de
  asistencia, costo por agenda y por asistencia; ordenables con clic en el encabezado.
- Cada campana tiene un color suave (paleta `PALETTE` en la plantilla, asignado por
  inversion) y sus anuncios se pintan del mismo color.
- La fila "Anuncios sin inversion en el periodo" agrupa personas que escribieron por
  anuncios sin gasto en el periodo; queda siempre al final y sin color.
- El costo por asistencia mas bajo de cada tabla se marca en verde.

## Criterios de medicion
- Se cuentan **personas** (ai_customer de Vambe: une WhatsApp e Instagram de una persona).
- Cada persona se atribuye al **primer anuncio** desde el que escribio en el periodo.
- **Agendo** = tiene una hora creada despues de ese primer mensaje, aunque la hora sea
  posterior al periodo. Por eso conviene generar el informe ~2-3 semanas despues del cierre
  del periodo; si se regenera mas tarde los numeros pueden subir levemente.
- Cada persona cae en un solo estado por su mejor resultado:
  asistio > no se presento > pendiente > cancelo.
- **Semanas** = bloques de 7 dias desde `since` (el ultimo puede ser mas corto). Agendas y
  asistencias van a la semana del primer mensaje, para compararlas con la inversion de esa
  semana.
- "Pendientes" = horas aun agendadas/confirmadas en el sistema de reservas; algunas ya pasadas
  pueden faltar por actualizar.

## Archivos
- `generate_vambe_report.py` — combina Meta (en vivo) + JSON de Vambe y renderiza. Reusa
  helpers de `../reporte-cuenta/generate_report.py` (API, miniaturas, nombres de anuncios).
- `report_template.html` — plantilla; se auto-renderiza desde `DATA`.
- `vambe_query.sql` — consulta de export de Vambe.
