# 📊 Avance 2 — Ejecución y Análisis de Queries (FleetLogix)

Documentación de las 12 queries proporcionadas por la cátedra: resultado, problema de negocio, plan de ejecución (`EXPLAIN ANALYZE`) antes de indexar, y comparación después de crear los índices.

---

## QUERIES BÁSICAS

### Query 1 — Contar vehículos por tipo

**Problema de negocio:** conocer la composición actual de la flota (cuántos vehículos hay de cada tipo), para planificación de recursos y mantenimiento.

**Resultado:**

| vehicle_type | cantidad |
|---|---|
| Van | 69 |
| Camión Grande | 60 |
| Camión Mediano | 51 |
| Motocicleta | 20 |

**Plan de ejecución (ANTES de indexar):**

```
Sort  (cost=5.08..5.09 rows=4 width=19) (actual time=0.084..0.085 rows=4.00 loops=1)
  Sort Key: (count(*)) DESC
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=2
  ->  HashAggregate  (cost=5.00..5.04 rows=4 width=19) (actual time=0.076..0.077 rows=4.00 loops=1)
        Group Key: vehicle_type
        Batches: 1  Memory Usage: 32kB
        Buffers: shared hit=2
        ->  Seq Scan on vehicles  (cost=0.00..4.00 rows=200 width=11) (actual time=0.020..0.031 rows=200.00 loops=1)
              Buffers: shared hit=2
Planning Time: 0.110 ms
Execution Time: 0.122 ms
```

**Análisis:** con solo 200 filas en `vehicles`, el `Seq Scan` (escaneo secuencial completo) es la estrategia correcta — no tiene sentido usar un índice para una tabla tan chica, Postgres la recorre entera más rápido de lo que tardaría en consultar un índice. Tiempo de ejecución: **0.122 ms**. Esta query no está entre las que se benefician de los 5 índices propuestos (son todos sobre `trips`, `deliveries`, `maintenance`, `drivers`, `routes`).

---

### Query 2 — Conductores con licencia próxima a vencer

**Problema de negocio:** identificar a los conductores cuya licencia vence dentro de los próximos 30 días, para prevenir que sigan operando con documentación vencida (riesgo legal/operativo).

**Resultado:** 15 conductores con vencimiento entre el 15/07/2026 y el 22/08/2026 (ej. Milena Galeano — 15/07/2026, Rocío Valencia — 20/07/2026, ...).

**Plan de ejecución (ANTES de indexar):**

```
Sort  (cost=12.27..12.30 rows=14 width=29) (actual time=0.229..0.231 rows=15.00 loops=1)
  Sort Key: license_expiry
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=5
  ->  Seq Scan on drivers  (cost=0.00..12.00 rows=14 width=29) (actual time=0.028..0.202 rows=15.00 loops=1)
        Filter: (license_expiry < (CURRENT_DATE + '30 days'::interval))
        Rows Removed by Filter: 385
        Buffers: shared hit=5
Planning Time: 0.124 ms
Execution Time: 0.248 ms
```

**Análisis:** `drivers` tiene 400 filas — Postgres las recorre todas (`Seq Scan`), descarta 385 que no cumplen el filtro y ordena las 15 restantes. Al igual que en la Query 1, la tabla es demasiado chica para que un índice aporte mejora real. Tiempo de ejecución: **0.248 ms**.

---

### Query 3 — Total de viajes por estado

**Problema de negocio:** monitorear cuántos viajes están en curso vs. finalizados, para tener visibilidad operativa en tiempo real.

**Resultado:** `completed → 100,000` (único estado presente; no hay viajes `in_progress` en la base actual).

**Plan de ejecución (ANTES de indexar):**

```
HashAggregate  (cost=2531.00..2531.01 rows=1 width=18) (actual time=25.495..25.496 rows=1.00 loops=1)
  Group Key: status
  Batches: 1  Memory Usage: 32kB
  Buffers: shared hit=1031
  ->  Seq Scan on trips  (cost=0.00..2031.00 rows=100000 width=10) (actual time=0.023..6.051 rows=100000.00 loops=1)
        Buffers: shared hit=1031
Planning Time: 0.138 ms
Execution Time: 25.548 ms
```

**Análisis:** primer salto notorio de tiempo (25.548 ms vs. <1ms en las queries 1 y 2), explicado por el tamaño de `trips` (100,000 filas vs. 200/400). El `Seq Scan` recorre la tabla entera (1,031 buffers leídos) porque no hay ningún índice sobre `status`, y al ser una columna con muy poca variedad de valores (en la práctica, solo `completed`), un índice tradicional tampoco ayudaría demasiado — Postgres igual tendría que leer casi todas las filas. Esto conecta con el patrón de negocio #2 detectado en el Avance 1 (`status` solo contempla `completed`/`in_progress`, sin cancelaciones): acá se confirma con datos reales que el 100% de los viajes generados quedó en `completed`.

---

## QUERIES INTERMEDIAS

### Query 4 — Entregas por ciudad destino (últimos 60 días)

**Problema de negocio:** identificar la demanda de entregas por ciudad para planificar recursos (vehículos, personal) según dónde se concentra la operación.

**Resultado:**

| Ciudad | Viajes | Entregas | Peso total (kg) |
|---|---|---|---|
| Bogotá | 498 | 2,022 | 831,937.12 |
| Cartagena | 460 | 1,829 | 792,229.94 |
| Medellín | 383 | 1,508 | 635,700.96 |
| Barranquilla | 372 | 1,463 | 659,242.61 |
| Cali | 353 | 1,420 | 572,631.32 |

**Plan de ejecución (ANTES de indexar):**

```
Sort  (cost=11759.53..11759.54 rows=5 width=57) (actual time=36.623..37.008 rows=5.00 loops=1)
  Sort Key: (count(d.delivery_id)) DESC
  Buffers: shared hit=3740 read=6447
  ->  GroupAggregate (actual time=35.101..37.001 rows=5.00 loops=1)
        Group Key: r.destination_city
        ->  Gather Merge  (Workers Planned: 2, Workers Launched: 2) (actual time=34.777..36.200 rows=8242.00 loops=1)
              ->  Sort (actual time=30.213..30.291 rows=2747.33 loops=3)
                    ->  Hash Join  (Hash Cond: t.route_id = r.route_id) (actual time=1.605..28.222 rows=2747.33 loops=3)
                          ->  Hash Join  (Hash Cond: d.trip_id = t.trip_id) (actual time=1.465..27.441 rows=2747.33 loops=3)
                                ->  Parallel Seq Scan on deliveries d (actual time=0.137..11.124 rows=133333.33 loops=3)
                                ->  Hash (rows=2066)
                                      ->  Index Scan using idx_trips_departure on trips t (actual time=0.032..0.975 rows=2066.00 loops=3)
                                            Index Cond: (departure_datetime >= (CURRENT_DATE - '60 days'::interval))
                          ->  Hash (rows=50) -> Seq Scan on routes r
Planning Time: 0.470 ms
Execution Time: 37.074 ms
```

**Análisis:** query con 3 tablas y JOIN pesado sobre `deliveries` (400,000 filas). Ya existe un índice `idx_trips_departure` (del schema original, no de los 5 propuestos) que Postgres usa eficientemente para filtrar por fecha. El cuello de botella real es el `Parallel Seq Scan on deliveries` — Postgres decide paralelizar la query (2 workers) por el volumen de datos. Ninguno de los 5 índices propuestos cubre el JOIN `deliveries.trip_id = trips.trip_id`, que es donde más se podría optimizar. Tiempo de ejecución: **37.074 ms**.

---

### Query 5 — Conductores activos con viajes completados

**Problema de negocio:** evaluar la carga de trabajo por conductor (cuántos viajes hizo cada uno), para balancear asignaciones y detectar sobre/subutilización.

**Resultado (top 10 de 373 conductores activos):**

| driver_id | Nombre | Vencimiento licencia | Viajes totales | Completados |
|---|---|---|---|---|
| 341 | Rosalba Ayala | 2028-10-06 | 311 | 311 |
| 7 | Laura Bautista | 2027-07-20 | 310 | 310 |
| 265 | Patricia Martínez | 2027-11-05 | 308 | 308 |
| 347 | Alexander Montoya | 2028-06-27 | 308 | 308 |
| 57 | Fabio Alvarado | 2027-05-10 | 306 | 306 |

**Plan de ejecución (ANTES de indexar):**

```
Sort  (cost=3253.04..3253.35 rows=124 width=70) (actual time=40.879..40.889 rows=373.00 loops=1)
  Sort Key: (sum(CASE WHEN status='completed' THEN 1 ELSE 0 END)) DESC
  Buffers: shared hit=1036
  ->  HashAggregate (actual time=40.782..40.829 rows=373.00 loops=1)
        Group Key: d.driver_id
        Filter: (count(t.trip_id) > 0)
        ->  Hash Right Join  (Hash Cond: t.driver_id = d.driver_id) (actual time=0.318..24.497 rows=100000.00 loops=1)
              ->  Seq Scan on trips t (actual time=0.008..5.183 rows=100000.00 loops=1)
                    Buffers: shared hit=1031
              ->  Hash (rows=373)
                    ->  Seq Scan on drivers d (actual time=0.018..0.080 rows=373.00 loops=1)
                          Filter: (status = 'active')
                          Rows Removed by Filter: 27
Planning Time: 0.258 ms
Execution Time: 40.955 ms
```

**Análisis:** de los 400 conductores, 373 están `active` (27 descartados). El plan hace `Seq Scan` completo tanto en `trips` (100,000 filas, para el JOIN por `driver_id`) como en `drivers` (filtro por `status`). Esta query es candidata directa a **dos** de los 5 índices propuestos: `idx_drivers_status_license` (cubre el filtro `status = 'active'`) e `idx_trips_composite_joins` (cubre `driver_id` en el JOIN). Buen caso para comparar antes/después. Tiempo de ejecución: **40.955 ms**.

---

### Query 6 — Promedio de entregas por conductor (últimos 6 meses)

**Problema de negocio:** medir la productividad individual de cada conductor (entregas por viaje y por día), para evaluaciones de desempeño e incentivos.

**Resultado (top 10 de 373 conductores con ≥10 viajes):**

| driver_id | Conductor | Viajes | Entregas | Prom./viaje | Prom./día |
|---|---|---|---|---|---|
| 183 | Duván Álvarez | 55 | 245 | 4.45 | 1.36 |
| 126 | Camila Portilla | 40 | 177 | 4.43 | 0.98 |
| 223 | Andrés Muñoz | 52 | 230 | 4.42 | 1.28 |
| 262 | Jesús Castañeda | 54 | 237 | 4.39 | 1.32 |
| 337 | Milena Valencia | 56 | 243 | 4.34 | 1.35 |

**Plan de ejecución (ANTES de indexar):**

```
Sort (actual time=104.813..104.820 rows=373.00 loops=1)
  Buffers: shared hit=10765 read=5977, temp read=370 written=371
  ->  GroupAggregate (actual time=97.161..104.727 rows=373.00 loops=1)
        Filter: (count(DISTINCT t.trip_id) >= 10)
        ->  Sort  (Sort Method: external merge  Disk: 2960kB) (actual time=97.126..101.189 rows=78031.00 loops=1)
              Sort Key: dr.driver_id, t.trip_id
              ->  Hash Join  (Hash Cond: t.driver_id = dr.driver_id) (actual time=8.774..67.844 rows=78031.00 loops=1)
                    ->  Hash Join  (Hash Cond: d.trip_id = t.trip_id) (actual time=8.636..58.386 rows=78031.00 loops=1)
                          ->  Seq Scan on deliveries d (actual time=0.127..16.339 rows=400000.00 loops=1)
                          ->  Hash (rows=19592)
                                ->  Index Scan using idx_trips_departure on trips t (actual time=0.034..6.080 rows=19490.00 loops=1)
                                      Index Cond: (departure_datetime >= (CURRENT_DATE - '6 mons'::interval))
                                      Filter: (status = 'completed')
                    ->  Hash (rows=400) -> Seq Scan on drivers dr
Planning Time: 0.518 ms
Execution Time: 105.494 ms
```

**Análisis:** la más lenta hasta ahora. Dos hallazgos clave: (1) el `Sort Method: external merge Disk: 2960kB` indica que Postgres se quedó sin memoria de trabajo (`work_mem`) para ordenar ~78,000 filas y tuvo que usar disco temporal, mucho más lento que ordenar en RAM; (2) el `Seq Scan on deliveries` (400,000 filas) vuelve a ser el mayor costo bruto, y de nuevo ningún índice de los 5 propuestos cubre ese acceso directamente (siguen apuntando a `trips`, `drivers`, `maintenance`, `routes`). El índice `idx_trips_departure` sí ayuda en el filtro de fecha+status sobre `trips`. Tiempo de ejecución: **105.494 ms**.

---

### Query 7 — Rutas con mayor consumo de combustible por km

**Problema de negocio:** identificar las rutas menos eficientes en consumo de combustible, para priorizar optimización operativa (mantenimiento, cambio de vehículo asignado, revisión de trazado).

**Resultado (top 10 rutas con más litros/100km):**

| Ruta | Código | Distancia (km) | Viajes | Litros/100km |
|---|---|---|---|---|
| Barranquilla → Bogotá | R009 | 457.69 | 2,013 | 11.60 |
| Cartagena → Medellín | R043 | 515.76 | 1,965 | 11.59 |
| Bogotá → Medellín | R025 | 401.39 | 1,944 | 11.58 |
| Medellín → Cartagena | R003 | 540.52 | 1,949 | 11.56 |
| Medellín → Cali | R038 | 542.04 | 1,924 | 11.56 |

**Plan de ejecución (ANTES de indexar):**

```
Limit (actual time=39.150..40.065 rows=10.00 loops=1)
  ->  Sort (Sort Method: top-N heapsort) (actual time=39.149..40.063 rows=10.00 loops=1)
        ->  Finalize GroupAggregate (actual time=39.034..40.045 rows=50.00 loops=1)
              Filter: (count(t.trip_id) >= 50)
              ->  Gather Merge (Workers Planned: 1, Workers Launched: 1) (actual time=39.023..39.956 rows=100.00 loops=1)
                    ->  Sort (actual time=35.857..35.859 rows=50.00 loops=2)
                          ->  Partial HashAggregate (actual time=35.715..35.729 rows=50.00 loops=2)
                                ->  Hash Join  (Hash Cond: t.route_id = r.route_id) (actual time=0.122..16.084 rows=50000.00 loops=2)
                                      ->  Parallel Seq Scan on trips t (actual time=0.012..7.263 rows=50000.00 loops=2)
                                            Filter: (fuel_consumed_liters IS NOT NULL AND status = 'completed')
                                      ->  Hash (rows=50)
                                            ->  Seq Scan on routes r (actual time=0.059..0.071 rows=50.00 loops=2)
                                                  Filter: (distance_km > 0)
Planning Time: 0.334 ms
Execution Time: 40.151 ms
```

**Análisis:** vuelve a aparecer `Parallel Seq Scan on trips` (100,000 filas) con doble filtro (`fuel_consumed_liters IS NOT NULL` + `status = 'completed'`) — esto es justo el tipo de acceso que cubre parcialmente `idx_trips_composite_joins` (incluye `route_id` y tiene la condición `WHERE status='completed'`). El `Seq Scan on routes` es liviano (solo 50 filas). Buena candidata para medir mejora con `idx_trips_composite_joins` y `idx_routes_metrics`. Tiempo de ejecución: **40.151 ms**.

---

### Query 8 — Entregas retrasadas por día de la semana

**Problema de negocio:** identificar patrones de retraso según el día de la semana, para ajustar planificación de rutas y personal en los días más problemáticos.

**Resultado:**

| Día | Entregas | Retrasadas | % Retraso | Min. prom. diferencia |
|---|---|---|---|---|
| Domingo | 4,054 | 403 | 9.94% | 12.11 |
| Lunes | 3,706 | 358 | 9.66% | 11.39 |
| Martes | 3,479 | 339 | 9.74% | 11.38 |
| Miércoles | 3,480 | 357 | 10.26% | 11.85 |
| Jueves | 3,402 | 344 | 10.11% | 11.80 |
| Viernes | 3,481 | 361 | 10.37% | 12.39 |
| Sábado | 3,968 | 386 | 9.73% | 11.63 |

**Plan de ejecución (ANTES de indexar):**

```
GroupAggregate (actual time=45.287..52.977 rows=7.00 loops=1)
  Group Key: EXTRACT(dow FROM scheduled_datetime), to_char(scheduled_datetime, 'Day')
  Buffers: shared hit=2158 read=5225
  ->  Gather Merge  (Workers Planned: 2, Workers Launched: 2) (actual time=44.033..47.636 rows=25570.00 loops=1)
        ->  Sort (actual time=40.339..40.609 rows=8523.33 loops=3)
              Sort Method: quicksort  Memory: 878kB
              ->  Parallel Seq Scan on deliveries d (actual time=4.132..38.258 rows=8523.33 loops=3)
                    Filter: (delivery_status = 'delivered' AND scheduled_datetime >= (CURRENT_DATE - '90 days'))
                    Rows Removed by Filter: 124810
                    Buffers: shared hit=2068 read=5225
Planning Time: 0.198 ms
Execution Time: 53.024 ms
```

**Análisis:** esta es la query objetivo directa del índice `idx_deliveries_scheduled_datetime` (sobre `scheduled_datetime` + `delivery_status`, `WHERE delivery_status='delivered'`). El plan actual hace `Parallel Seq Scan` sobre `deliveries` y descarta 124,810 filas por el filtro combinado de fecha+status — exactamente el patrón que un índice parcial resuelve bien. Buen candidato para mostrar mejora clara antes/después. Tiempo de ejecución: **53.024 ms**.

---

## QUERIES COMPLEJAS

### Query 9 — Costo de mantenimiento por km recorrido (CTE)

**Problema de negocio:** evaluar costo-beneficio de cada tipo de vehículo (relación mantenimiento/km recorrido), para decisiones de renovación o reasignación de flota.

**Resultado:**

| Tipo | Vehículos | Km totales | Costo/km |
|---|---|---|---|
| Camión Grande | 53 | 460,259,830.08 | 555.26 |
| Motocicleta | 19 | 162,207,469.11 | 553.41 |
| Van | 62 | 542,200,603.83 | 550.37 |
| Camión Mediano | 47 | 405,514,660.66 | 545.93 |

**Plan de ejecución (ANTES de indexar):**

```
Sort (actual time=2451.034..2451.037 rows=4.00 loops=1)
  Buffers: shared hit=1120, temp read=38904 written=38965
  ->  GroupAggregate  (Group Key: vehicle_metrics.vehicle_type) (actual time=2451.002..2451.029 rows=4.00 loops=1)
        ->  Sort (actual time=2450.985..2450.992 rows=181.00 loops=1)
              ->  Subquery Scan on vehicle_metrics (actual time=1926.793..2450.875 rows=181.00 loops=1)
                    ->  GroupAggregate  (Group Key: v.license_plate)
                          Filter: (sum(r.distance_km) > 0 AND sum(m.cost) > 0)
                          ->  Sort  (Sort Method: external merge  Disk: 155640kB) (actual time=1924.098..2163.772 rows=2768552.00 loops=1)
                                Sort Key: v.license_plate, t.trip_id
                                ->  Hash Join  (Hash Cond: t.vehicle_id = v.vehicle_id) (actual time=2.172..174.580 rows=2768552.00 loops=1)
                                      ->  Hash Left Join (t JOIN routes r) (actual time=0.049..18.283 rows=100000.00 loops=1)
                                            ->  Seq Scan on trips t
                                                  Filter: (status = 'completed')
                                            ->  Hash -> Seq Scan on routes r
                                      ->  Hash (rows=5019)
                                            ->  Hash Right Join (m JOIN v) (actual time=0.062..1.395 rows=5019.00 loops=1)
                                                  ->  Seq Scan on maintenance m
                                                  ->  Hash -> Seq Scan on vehicles v
Planning Time: 0.576 ms
Execution Time: 2476.846 ms
```

**Análisis:** esta es la query más pesada de las 12 en tiempo de ejecución (**2,476.846 ms**). La causa principal está en el volumen de filas que procesa internamente: el `Sort` intermedio (antes de agregar por `vehicle_id`) maneja **2,768,552 filas**, resultado de combinar los 100,000 `trips` con los 5,000 registros de `maintenance` a través de dos JOINs encadenados sobre `vehicles`. Ese volumen no cabe en la memoria de trabajo asignada, por lo que Postgres recurre a un `Sort Method: external merge Disk: 155640kB` — 155 MB escritos a disco temporal —, lo cual explica gran parte del tiempo total. Es un buen ejemplo de cómo una CTE con múltiples `LEFT JOIN` sobre relaciones 1:N puede generar un volumen de datos intermedio mucho mayor al tamaño de las tablas originales, con impacto directo en performance.

---

### Query 10 — Ranking de eficiencia de conductores (Window Functions)

**Problema de negocio:** identificar a los conductores top performers combinando puntualidad, eficiencia de combustible y productividad, para asignar incentivos con un criterio objetivo y ponderado.

**Resultado (top 10, menor score = mejor):**

| Conductor | Viajes | Entregas | Consumo/100km | Puntualidad % | Rank punt. | Rank efic. | Rank prod. | Score |
|---|---|---|---|---|---|---|---|---|
| Esther Herrera | 24 | 91 | 10.36 | 61.54 | 1 | 2 | 38 | 13.67 |
| María Montoya | 30 | 114 | 11.37 | 56.14 | 4 | 43 | 6 | 17.67 |
| María Cardona | 27 | 104 | 11.16 | 50.96 | 20 | 26 | 12 | 19.33 |
| Cecilia Valencia | 24 | 96 | 11.27 | 54.17 | 8 | 31 | 27 | 22.00 |
| Ernesto Marín | 25 | 105 | 11.37 | 52.38 | 12 | 45 | 10 | 22.33 |

**Plan de ejecución (ANTES de indexar):**

```
Limit (actual time=75.167..75.170 rows=20.00 loops=1)
  ->  Sort (Sort Method: top-N heapsort) (actual time=75.165..75.168 rows=20.00 loops=1)
        ->  WindowAgg (Window: w3 ORDER BY puntualidad_pct) (actual time=75.109..75.144 rows=106.00 loops=1)
              ->  Sort (Sort Key: puntualidad_pct DESC) (actual time=75.106..75.110 rows=106.00 loops=1)
                    ->  WindowAgg (Window: w2 ORDER BY consumo_100km) (actual time=75.070..75.093 rows=106.00 loops=1)
                          ->  Sort (Sort Key: consumo_100km) (actual time=75.069..75.073 rows=106.00 loops=1)
                                ->  WindowAgg (Window: w1 ORDER BY entregas) (actual time=75.035..75.056 rows=106.00 loops=1)
                                      ->  Sort (Sort Key: entregas DESC) (actual time=75.032..75.036 rows=106.00 loops=1)
                                            ->  Subquery Scan on conductor_metricas (actual time=68.928..75.017 rows=106.00 loops=1)
                                                  ->  GroupAggregate (Group Key: d.driver_id) (actual time=68.927..75.009 rows=106.00 loops=1)
                                                        Filter: (count(DISTINCT t.trip_id) >= 20)
                                                        Rows Removed by Filter: 267
                                                        ->  Sort (rows=26147.00 loops=1)
                                                              ->  Hash Join (t.route_id = r.route_id) (actual time=2.553..60.576)
                                                                    ->  Hash Join (t.driver_id = d.driver_id) (actual time=2.512..56.997)
                                                                          ->  Hash Right Join (del.trip_id = t.trip_id) (actual time=2.404..52.848)
                                                                                ->  Seq Scan on deliveries del (400,000 filas) (actual time=0.127..18.113)
                                                                                ->  Hash (rows=6422)
                                                                                      ->  Index Scan using idx_trips_departure on trips t
                                                                                            Index Cond: (departure_datetime >= CURRENT_DATE - '3 mons')
                                                                          ->  Hash -> Seq Scan on drivers d
                                                                    ->  Hash -> Seq Scan on routes r
Planning Time: 0.637 ms
Execution Time: 75.269 ms
```

**Análisis:** a diferencia de la Query 9, acá el JOIN con `deliveries` es correcto (`del.trip_id = t.trip_id`, relación real 1:N sin duplicación indebida), por eso los resultados sí son coherentes con la realidad del negocio. Punto técnico destacable: como cada `RANK() OVER (...)` ordena por una columna distinta (`puntualidad_pct`, `consumo_100km`, `entregas`), Postgres no puede reutilizar un solo `Sort` — genera **tres pasadas `Sort + WindowAgg` en cascada**, una por cada ventana. El `idx_trips_departure` ayuda en el filtro de fecha, pero el `Seq Scan on deliveries` (400,000 filas) sigue siendo el mayor costo. Tiempo de ejecución: **75.269 ms**.

---

### Query 11 — Tendencia mensual de viajes (LAG/LEAD)

**Problema de negocio:** proyectar necesidades futuras de flota y personal analizando la tendencia mes a mes (crecimiento/caída, promedio móvil), en vez de mirar solo el dato puntual de un mes.

**Resultado (últimos 12 meses, orden descendente):**

| Período | Viajes | Mes anterior | Mes siguiente | Cambio abs. | Cambio % | Ton. transportadas | Prom. móvil 3m |
|---|---|---|---|---|---|---|---|
| 2026-06 | 1,058 | 4,464 | — | -3,406 | -76.30% | 1,861.50 | 3,280.67 |
| 2026-05 | 4,464 | 4,320 | 1,058 | 144 | 3.33% | 8,098.34 | 4,416.00 |
| 2026-04 | 4,320 | 4,464 | 4,464 | -144 | -3.23% | 7,714.22 | 4,272.00 |
| 2026-03 | 4,464 | 4,032 | 4,320 | 432 | 10.71% | 8,118.18 | 4,320.00 |
| 2026-02 | 4,032 | 4,464 | 4,464 | -432 | -9.68% | 7,254.06 | 4,320.00 |

**Plan de ejecución (ANTES de indexar):**

```
Limit (actual time=50.345..50.346 rows=12.00 loops=1)
  Buffers: shared hit=1031, temp read=392 written=393
  ->  Sort  (Sort Key: date_trunc('month', departure_datetime) DESC) (actual time=50.343..50.344 rows=12.00 loops=1)
        ->  WindowAgg (Window: w2 ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) (actual time=38.198..50.330 rows=24.00 loops=1)
              ->  WindowAgg (Window: w1 ORDER BY date_trunc('month', departure_datetime)) (actual time=37.588..50.273 rows=24.00 loops=1)
                    ->  GroupAggregate  (Group Key: date_trunc('month', departure_datetime)) (actual time=37.037..50.243 rows=24.00 loops=1)
                          ->  Sort  (Sort Method: external merge  Disk: 3136kB) (actual time=36.713..40.766 rows=100000.00 loops=1)
                                Sort Key: date_trunc('month', departure_datetime)
                                ->  Seq Scan on trips (actual time=0.029..21.722 rows=100000.00 loops=1)
                                      Filter: (status = 'completed')
Planning Time: 0.235 ms
Execution Time: 50.990 ms
```

**Análisis:** junio 2026 muestra solo 1,058 viajes frente a un promedio de ~4,300-4,464 en los meses anteriores — no es una caída real del negocio, sino el mes parcial en el que se generaron los datos sintéticos (corte a mitad de mes), y hay que aclararlo así en la documentación para no leerlo como una alerta operativa. A nivel plan: `Seq Scan on trips` (100,000 filas) + un `Sort` que vuelve a caer a disco (`external merge Disk: 3136kB`) porque agrupar por `DATE_TRUNC('month', ...)` requiere ordenar todo el dataset antes de agregar — ninguno de los 5 índices propuestos cubre una expresión calculada como `DATE_TRUNC`, así que esta query no se beneficiaría de ellos tal como están definidos (un índice funcional sobre `DATE_TRUNC('month', departure_datetime)` sí lo haría, pero no es parte de los 5 provistos). Tiempo de ejecución: **50.990 ms**.

---

### Query 12 — Pivot de entregas por hora y día de la semana

**Problema de negocio:** identificar los horarios y días de mayor volumen de entregas, para optimizar turnos de personal y ventanas de reparto.

**Resultado (extracto — franja de mayor actividad):**

| Hora | Lun | Mar | Mié | Jue | Vie | Sáb | Dom | Total |
|---|---|---|---|---|---|---|---|---|
| 11 | 81 | 98 | 73 | 91 | 63 | 85 | 92 | 583 |
| 12 | 69 | 98 | 84 | 87 | 63 | 72 | 94 | 567 |
| 13 | 85 | 106 | 94 | 88 | 67 | 70 | 92 | 602 |

Pico de entregas entre las **11:00 y 13:00**.

**Plan de ejecución (ANTES de indexar):**

```
GroupAggregate (Group Key: entregas_por_hora_dia.hora) (actual time=52.907..53.218 rows=17.00 loops=1)
  Buffers: shared hit=4116 read=3251
  ->  Sort (Sort Key: hora) (actual time=52.899..53.181 rows=119.00 loops=1)
        ->  Subquery Scan on entregas_por_hora_dia (actual time=51.389..53.161 rows=119.00 loops=1)
              ->  GroupAggregate  (Group Key: EXTRACT(dow), EXTRACT(hour)) (actual time=51.388..53.155 rows=119.00 loops=1)
                    ->  Gather Merge  (Workers Planned: 2, Workers Launched: 2) (actual time=51.379..52.680 rows=7311.00 loops=1)
                          ->  Sort (actual time=47.321..47.378 rows=2437.00 loops=3)
                                ->  Parallel Seq Scan on deliveries (actual time=0.183..46.011 rows=2437.00 loops=3)
                                      Filter: (EXTRACT(hour) >= 6 AND EXTRACT(hour) <= 22 AND scheduled_datetime >= CURRENT_DATE - 60 days)
                                      Rows Removed by Filter: 130896
Planning Time: 0.238 ms
Execution Time: 53.269 ms
```

**Análisis:** dato técnico destacable — el filtro `hora BETWEEN 6 AND 22` del `SELECT` externo, Postgres lo empuja ("pushdown") directo al `Parallel Seq Scan on deliveries`, aplicándolo junto con el filtro de fecha desde el arranque del escaneo, en vez de traer todas las filas y filtrar después. Aun así, sigue siendo un escaneo paralelo sobre `deliveries` (400,000 filas), y el índice `idx_deliveries_scheduled_datetime` puede ayudar parcialmente en la parte del filtro de fecha (aunque su condición `WHERE delivery_status='delivered'` no aplica acá, porque esta query no filtra por `delivery_status`). Tiempo de ejecución: **53.269 ms**.

---

## 📋 Resumen — Tiempos ANTES de indexar

| # | Query | Tiempo (ms) | ¿Seleccionada para entrega? |
|---|---|---|---|
| 1 | Vehículos por tipo | 0.122 | ✅ |
| 2 | Licencias por vencer | 0.248 | — |
| 3 | Viajes por estado | 25.548 | ✅ |
| 4 | Entregas por ciudad (60 días) | 37.074 | — |
| 5 | Conductores activos con viajes | 40.955 | ✅ |
| 6 | Promedio entregas por conductor (6m) | 105.494 | — |
| 7 | Rutas con mayor consumo combustible | 40.151 | ✅ |
| 8 | Entregas retrasadas por día | 53.024 | ✅ |
| 9 | Costo mantenimiento por km (CTE) | 2,476.846 | ✅ |
| 10 | Ranking eficiencia conductores (Window) | 75.269 | ✅ |
| 11 | Tendencia mensual (LAG/LEAD) | 50.990 | — |
| 12 | Pivot entregas por hora/día | 53.269 | ✅ |

**8 queries seleccionadas para el entregable:** 1, 3, 5, 7, 8, 9, 10, 12 — cubren las 3 categorías (básicas, intermedias, complejas) y la mayoría conecta directamente con los 5 índices propuestos, permitiendo medir mejora real.

---

## 🚀 Índices creados

```sql
CREATE INDEX idx_trips_composite_joins ON trips(vehicle_id, driver_id, route_id, departure_datetime)
WHERE status = 'completed';

CREATE INDEX idx_deliveries_scheduled_datetime ON deliveries(scheduled_datetime, delivery_status)
WHERE delivery_status = 'delivered';

CREATE INDEX idx_maintenance_vehicle_cost ON maintenance(vehicle_id, cost);

CREATE INDEX idx_drivers_status_license ON drivers(status, license_expiry)
WHERE status = 'active';

CREATE INDEX idx_routes_metrics ON routes(route_id, distance_km, destination_city);
```

Ejecutados con éxito, seguidos de `ANALYZE` sobre las 6 tablas para actualizar estadísticas del planificador.

---

## 📊 Comparación ANTES / DESPUÉS de indexar (8 queries seleccionadas)

### Query 1 — Vehículos por tipo (DESPUÉS)

```
Sort  (cost=5.08..5.09 rows=4 width=19) (actual time=0.161..0.163 rows=4.00 loops=1)
  ->  HashAggregate (actual time=0.146..0.148 rows=4.00 loops=1)
        ->  Seq Scan on vehicles (actual time=0.021..0.045 rows=200.00 loops=1)
Planning Time: 0.294 ms
Execution Time: 0.229 ms
```

**Comparación:** Antes 0.122 ms → Después 0.229 ms. Plan **idéntico** (`Seq Scan`, sin uso de ningún índice nuevo). La diferencia de tiempo es ruido de medición normal (variación de caché/sistema), no una mejora ni un empeoramiento real — esperable, ya que `vehicles` (200 filas) es demasiado chica para que un índice aporte algo, y ninguno de los 5 índices creados apunta a esta tabla.

---

### Query 3 — Total de viajes por estado (DESPUÉS)

```
HashAggregate (actual time=29.055..29.057 rows=1.00 loops=1)
  Group Key: status
  Buffers: shared hit=1031
  ->  Seq Scan on trips (actual time=0.037..7.879 rows=100000.00 loops=1)
        Buffers: shared hit=1031
Planning Time: 0.569 ms
Execution Time: 29.129 ms
```

**Comparación:** Antes 25.548 ms → Después 29.129 ms. Plan igual (`Seq Scan on trips`, sin índice). No hubo mejora — esperable, ya que `idx_trips_composite_joins` requiere `status = 'completed'` en el `WHERE`, pero esta query no filtra por `status`, solo lo agrupa, así que Postgres igual necesita leer toda la tabla. La ligera diferencia de tiempo es variación normal entre corridas.

---

### Query 5 — Conductores activos con viajes completados (DESPUÉS)

```
Sort (actual time=47.857..47.866 rows=373.00 loops=1)
  ->  HashAggregate (actual time=47.767..47.810 rows=373.00 loops=1)
        Filter: (count(t.trip_id) > 0)
        ->  Hash Right Join  (Hash Cond: t.driver_id = d.driver_id) (actual time=0.165..28.002 rows=100000.00 loops=1)
              ->  Seq Scan on trips t (actual time=0.005..5.648 rows=100000.00 loops=1)
              ->  Hash (rows=373)
                    ->  Seq Scan on drivers d (actual time=0.021..0.083 rows=373.00 loops=1)
                          Filter: (status = 'active')
                          Rows Removed by Filter: 27
Planning Time: 1.129 ms
Execution Time: 47.948 ms
```

**Comparación:** Antes 40.955 ms → Después 47.948 ms. **Sin mejora — plan idéntico, ningún índice usado.** Dos razones distintas, ambas legítimas para documentar:
1. `idx_drivers_status_license` no se usa porque el filtro `status = 'active'` no es selectivo: 373 de 400 filas (93%) lo cumplen, así que Postgres decide correctamente que `Seq Scan` es más barato que consultar el índice fila por fila.
2. `idx_trips_composite_joins` no se usa porque su condición parcial (`WHERE status='completed'`) exige que el filtro esté en el `WHERE`/`JOIN` de la query — acá `t.status` solo aparece dentro de un `CASE WHEN` del `SELECT`, no como filtro real, así que el índice parcial no aplica.

Este resultado es valioso para el informe: demuestra que **no todo índice "candidato" termina siendo usado**, y que la decisión depende de la selectividad real del filtro y de cómo está escrita la condición en la query.

---

### Query 7 — Rutas con mayor consumo de combustible (DESPUÉS)

```
Limit (actual time=39.101..39.943 rows=10.00 loops=1)
  ->  Sort (Sort Method: top-N heapsort) (actual time=39.100..39.941 rows=10.00 loops=1)
        ->  Finalize GroupAggregate (actual time=38.985..39.923 rows=50.00 loops=1)
              ->  Gather Merge (Workers Planned: 1, Workers Launched: 1) (actual time=38.973..39.843 rows=100.00 loops=1)
                    ->  Partial HashAggregate (actual time=35.392..35.431 rows=50.00 loops=2)
                          ->  Hash Join (Hash Cond: t.route_id = r.route_id) (actual time=0.100..15.847 rows=50000.00 loops=2)
                                ->  Parallel Seq Scan on trips t (actual time=0.011..7.187 rows=50000.00 loops=2)
                                      Filter: (fuel_consumed_liters IS NOT NULL AND status = 'completed')
                                ->  Hash (rows=50) -> Seq Scan on routes r
Planning Time: 2.220 ms
Execution Time: 40.024 ms
```

**Comparación:** Antes 40.151 ms → Después 40.024 ms. **Sin mejora real** (diferencia dentro del margen de ruido). `idx_trips_composite_joins` sigue sin usarse, y ahora tenemos la explicación completa del patrón: como el **100% de los 100,000 viajes tiene `status = 'completed'`** (ya confirmado en la Query 3), la condición parcial `WHERE status='completed'` del índice no excluye ninguna fila — no reduce el conjunto de datos en absoluto. Un índice parcial solo aporta cuando su condición filtra una porción significativa de la tabla; acá coincide con el 100%, por lo que Postgres correctamente prefiere `Seq Scan`. Este hallazgo explica por qué las queries 3, 5 y 7 no mostraron mejora, y es un punto de análisis fuerte para el informe: el índice está mal diseñado para los datos reales de esta base (aunque sea razonable en teoría).

---

### Query 8 — Entregas retrasadas por día de la semana (DESPUÉS)

```
GroupAggregate (actual time=43.072..48.608 rows=7.00 loops=1)
  Group Key: EXTRACT(dow FROM scheduled_datetime), to_char(scheduled_datetime, 'Day')
  Buffers: shared hit=4880 read=2338
  ->  Sort (actual time=42.026..42.761 rows=25570.00 loops=1)
        ->  Bitmap Heap Scan on deliveries d (actual time=6.000..35.861 rows=25570.00 loops=1)
              Recheck Cond: (scheduled_datetime >= CURRENT_DATE - '90 days' AND delivery_status = 'delivered')
              Heap Blocks: exact=7089
              ->  Bitmap Index Scan on idx_deliveries_scheduled_datetime (actual time=5.055..5.056 rows=25570.00 loops=1)
                    Index Cond: (scheduled_datetime >= CURRENT_DATE - '90 days')
Planning Time: 0.661 ms
Execution Time: 48.656 ms
```

**Comparación:** Antes 53.024 ms → Después 48.656 ms → **mejora del 8.2%**. Acá sí cambió el plan de verdad: pasó de `Parallel Seq Scan on deliveries` (recorrido paralelo completo) a `Bitmap Index Scan on idx_deliveries_scheduled_datetime` — Postgres usa el índice para localizar directamente las ~25,570 filas que cumplen el filtro de fecha, en vez de escanear las 400,000 filas de la tabla. La mejora es real pero moderada (no llega al 50%) porque el filtro de fecha (últimos 90 días) sigue devolviendo un volumen considerable de filas (~25,570), y además se necesita un `Bitmap Heap Scan` adicional para recuperar las columnas no incluidas en el índice.

---

### Query 9 — Costo de mantenimiento por km (CTE) (DESPUÉS)

```
Sort (actual time=605.886..605.902 rows=4.00 loops=1)
  ->  GroupAggregate (Group Key: vehicle_type) (actual time=605.859..605.896 rows=4.00 loops=1)
        ->  Sort (actual time=605.845..605.864 rows=181.00 loops=1)
              ->  Subquery Scan on vehicle_metrics (actual time=128.932..605.768 rows=181.00 loops=1)
                    ->  GroupAggregate (Group Key: v.license_plate) (actual time=128.931..605.725 rows=181.00 loops=1)
                          Filter: (sum(r.distance_km) > 0 AND sum(m.cost) > 0)
                          ->  Nested Loop Left Join (actual time=125.958..367.598 rows=2768552.00 loops=1)
                                ->  Gather Merge (Workers Planned: 1, Workers Launched: 1) (actual time=125.728..139.528 rows=100000.00 loops=1)
                                      ->  Sort (Sort Method: external merge Disk: 2376kB) (actual time=116.477..119.138 rows=50000.00 loops=2)
                                            ->  Hash Left Join (t.route_id = r.route_id) (actual time=0.197..23.987 rows=50000.00 loops=2)
                                                  ->  Hash Join (t.vehicle_id = v.vehicle_id) (actual time=0.134..16.123 rows=50000.00 loops=2)
                                                        ->  Parallel Seq Scan on trips t (actual time=0.009..7.328 rows=50000.00 loops=2)
                                                              Filter: (status = 'completed')
                                                        ->  Hash -> Seq Scan on vehicles v
                                                  ->  Hash -> Seq Scan on routes r
                                ->  Memoize (Cache Key: v.vehicle_id) (actual time=0.000..0.001 rows=27.69 loops=100000)
                                      Hits: 99819  Misses: 181  Evictions: 0  Memory Usage: 266kB
                                      ->  Index Scan using idx_maintenance_vehicle_cost on maintenance m (actual time=0.010..0.024 rows=27.62 loops=181)
                                            Index Cond: (vehicle_id = v.vehicle_id)
Planning Time: 3.365 ms
Execution Time: 606.488 ms
```

**Comparación:** Antes 2,476.846 ms → Después 606.488 ms → **mejora del 75.5%** ✅ (supera ampliamente el 50%+ que pide la consigna). El cambio de plan es radical: Postgres reemplazó el `Hash Right Join` contra toda la tabla `maintenance` por un `Nested Loop Left Join` + **`Memoize`** (caché de resultados) que usa `Index Scan using idx_maintenance_vehicle_cost` para buscar directamente el mantenimiento de cada vehículo por `vehicle_id`. El `Memoize` es clave: de 100,000 llamadas (una por cada fila de `trips`), solo 181 fueron "miss" real contra el índice — el resto (99,819) se resolvió desde caché en memoria, evitando recalcular lo mismo una y otra vez para el mismo vehículo. Este es el mejor ejemplo del entregable de cómo un índice bien dirigido puede cambiar por completo la estrategia de ejecución elegida por el planificador.

---

### Query 10 — Ranking de eficiencia de conductores (DESPUÉS)

```
Limit (actual time=111.495..111.498 rows=20.00 loops=1)
  Buffers: shared hit=10350 read=60
  ->  Sort (Sort Method: top-N heapsort) (actual time=111.494..111.496 rows=20.00 loops=1)
        ->  WindowAgg (w3, w2, w1 en cascada — igual que antes) (actual time=111.398..111.461 rows=106.00 loops=1)
              ->  GroupAggregate (Group Key: d.driver_id) (actual time=103.266..111.276 rows=106.00 loops=1)
                    ->  Sort (actual time=103.188..104.434 rows=26147.00 loops=1)
                          ->  Hash Join (t.route_id = r.route_id) (actual time=5.154..90.896 rows=26147.00 loops=1)
                                ->  Hash Join (t.driver_id = d.driver_id) (actual time=5.111..86.751 rows=26147.00 loops=1)
                                      ->  Hash Right Join (del.trip_id = t.trip_id) (actual time=4.897..77.458 rows=26147.00 loops=1)
                                            ->  Seq Scan on deliveries del (actual time=0.017..21.935 rows=400000.00 loops=1)
                                            ->  Hash (rows=6522)
                                                  ->  Index Scan using idx_trips_departure on trips t
Planning Time: 0.912 ms
Execution Time: 111.605 ms
```

**Comparación:** Antes 75.269 ms → Después 111.605 ms. **Sin mejora — plan idéntico** (mismo `Seq Scan on deliveries` de 400,000 filas, mismos JOINs). Ninguno de los 5 índices aplica acá: la query no filtra `deliveries` por `delivery_status` ni por `scheduled_datetime` (solo hace JOIN por `trip_id`, sin condición), así que `idx_deliveries_scheduled_datetime` no sirve. El aumento de tiempo respecto a la corrida anterior se atribuye a variabilidad del sistema (carga, caché), no a un efecto negativo de los índices — el plan de ejecución es exactamente el mismo antes y después.

---

### Query 12 — Pivot de entregas por hora/día (DESPUÉS)

```
GroupAggregate (Group Key: hora) (actual time=59.336..59.380 rows=17.00 loops=1)
  ->  Sort (Sort Key: hora) (actual time=59.329..59.345 rows=119.00 loops=1)
        ->  Subquery Scan on entregas_por_hora_dia (actual time=57.062..59.319 rows=119.00 loops=1)
              ->  GroupAggregate (Group Key: EXTRACT(dow), EXTRACT(hour)) (actual time=57.061..59.308 rows=119.00 loops=1)
                    ->  Gather Merge (Workers Planned: 2, Workers Launched: 2) (actual time=57.053..58.606 rows=7311.00 loops=1)
                          ->  Sort (actual time=52.306..52.387 rows=2437.00 loops=3)
                                ->  Parallel Seq Scan on deliveries (actual time=0.138..51.039 rows=2437.00 loops=3)
                                      Filter: (EXTRACT(hour) >= 6 AND EXTRACT(hour) <= 22 AND scheduled_datetime >= CURRENT_DATE - 60 days)
                                      Rows Removed by Filter: 130896
Planning Time: 0.342 ms
Execution Time: 59.418 ms
```

**Comparación:** Antes 53.269 ms → Después 59.418 ms. **Sin mejora — plan idéntico**, sigue en `Parallel Seq Scan`. `idx_deliveries_scheduled_datetime` no se usa porque su condición parcial (`WHERE delivery_status='delivered'`) no está presente en esta query — acá el filtro es solo por fecha y por hora (`EXTRACT(HOUR...)`, una expresión calculada que ningún índice de los 5 cubre). Mismo patrón visto en la Query 11: para que un índice ayude, su condición debe coincidir con cómo está escrito el filtro real de la query, no solo con la tabla involucrada.

---

## 📊 Tabla resumen final — Antes vs. Después (8 queries entregadas)

| # | Query | Antes (ms) | Después (ms) | Mejora | ¿Índice usado? |
|---|---|---|---|---|---|
| 1 | Vehículos por tipo | 0.122 | 0.229 | — (sin cambio) | No |
| 3 | Viajes por estado | 25.548 | 29.129 | — (sin cambio) | No |
| 5 | Conductores activos con viajes | 40.955 | 47.948 | — (sin cambio) | No |
| 7 | Rutas mayor consumo combustible | 40.151 | 40.024 | — (sin cambio) | No |
| 8 | Entregas retrasadas por día | 53.024 | 48.656 | **8.2%** | ✅ `idx_deliveries_scheduled_datetime` |
| 9 | Costo mantenimiento por km (CTE) | 2,476.846 | 606.488 | **75.5%** ✅ | ✅ `idx_maintenance_vehicle_cost` |
| 10 | Ranking eficiencia conductores | 75.269 | 111.605 | — (sin cambio) | No |
| 12 | Pivot entregas por hora/día | 53.269 | 59.418 | — (sin cambio) | No |

---

## 🎯 Conclusiones — Optimización con índices

1. **La mejora más significativa (75.5%)** se dio en la Query 9, gracias a `idx_maintenance_vehicle_cost`: cambió el plan de `Hash Join` (recorriendo `maintenance` entera) a `Nested Loop + Memoize + Index Scan`, evitando trabajo repetido por vehículo. Cumple de sobra el objetivo de mejora del 50%+ que pide la consigna.

2. **La Query 8 mostró mejora real pero moderada (8.2%)**: el plan cambió de `Seq Scan` a `Bitmap Index Scan` gracias a `idx_deliveries_scheduled_datetime`, aunque el filtro de fecha (90 días) sigue devolviendo un volumen considerable de filas.

3. **Las queries 1, 3, 5, 7, 10 y 12 no mostraron mejora**, y en todos los casos se identificó una razón concreta y documentable:
   - **Tablas muy chicas** (`vehicles`, `drivers`) donde `Seq Scan` ya es la estrategia óptima (Query 1).
   - **Filtros no selectivos**: la condición parcial `WHERE status='completed'` de `idx_trips_composite_joins` no filtra nada porque el 100% de los 100,000 viajes tiene ese estado (Queries 3, 5, 7).
   - **Condición del índice no coincide con el filtro real de la query**: `idx_deliveries_scheduled_datetime` requiere `delivery_status='delivered'` en el `WHERE`, pero las Queries 10 y 12 no filtran por esa columna (Query 10 solo hace JOIN sin filtro; Query 12 filtra por `EXTRACT(HOUR...)`, una expresión que ningún índice cubre).

4. **Hallazgo transversal:** de los 5 índices creados, solo **2 terminaron siendo usados** por el motor de queries (`idx_deliveries_scheduled_datetime` e `idx_maintenance_vehicle_cost`). Esto no invalida el ejercicio — al contrario, demuestra un entendimiento real de cómo Postgres decide usar o no un índice: selectividad del filtro, coincidencia exacta de la condición, y tamaño de la tabla son los factores que determinan si un índice "candidato" termina siendo "usado". Un índice bien diseñado en teoría puede no aportar nada si no calza con los filtros reales de las queries del negocio.

---

---

