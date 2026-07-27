# FleetLogix — Análisis de Arquitectura AWS

**Proyecto Integrador (Henry PIM2) — Avance 4: Arquitectura Cloud Serverless**

![Diagrama de arquitectura AWS](aws_architecture_diagram.png)

*Diagrama de arquitectura AWS propuesta: App Móvil → API Gateway → Lambda (Verificar Entrega, Calcular ETA, Alertas Desvíos) → S3 / DynamoDB / RDS (PostgreSQL migrada).*

> **Nota:** siguiendo la consigna del avance, esta arquitectura fue diseñada y documentada, pero no fue desplegada realmente en una cuenta de AWS. El objetivo del documento es demostrar comprensión del funcionamiento y las decisiones técnicas de cada servicio involucrado.

---

## 1. Resumen 

FleetLogix busca llevar su operación a la nube para procesar datos de flota en tiempo real: confirmaciones de entrega desde la app móvil de los conductores, cálculo de tiempo estimado de llegada (ETA) y detección de desvíos de ruta. La arquitectura propuesta es **serverless**: un **API Gateway** recibe las peticiones de la app móvil y las enruta a funciones **Lambda** sin servidores que administrar; los datos históricos se archivan en **S3** organizados por fecha; el **estado actual** de cada entrega y del tracking de vehículos vive en **DynamoDB** (lecturas/escrituras de baja latencia por clave); y la base operacional relacional (PostgreSQL, ya usada en los Avances 1-3) se migra a **RDS** para obtener backups, parcheo y alta disponibilidad administrados por AWS.

El diseño separa responsabilidades según el tipo de dato: lo **transaccional/histórico** (para análisis, auditoría, el Data Warehouse del Avance 3) permanece en PostgreSQL/RDS y S3; lo **operacional en tiempo real** (¿esta entrega ya se completó? ¿dónde está el camión ahora?) se resuelve con DynamoDB, pensado para lecturas de un solo ítem por clave con latencia de un dígito de milisegundos, algo para lo que una base relacional no es la herramienta más eficiente a esta escala de consultas frecuentes y simples.

---

## 2. Recibir y guardar datos: API Gateway, S3 y Lambda

### 2.1 API Gateway — puerta de entrada

API Gateway actúa como el único punto de entrada HTTPS para la app móvil de los conductores. En vez de que cada Lambda exponga su propio endpoint, API Gateway centraliza el enrutamiento, la autenticación/autorización (API Keys, IAM o un Authorizer con Cognito), la validación básica de payloads y el throttling (limitar peticiones por segundo para proteger las funciones Lambda de picos de tráfico). Se define un recurso REST por función de negocio, cada uno enrutado a su Lambda correspondiente mediante integración Lambda Proxy:

| Recurso / Ruta | Método | Lambda destino | Disparado por |
|---|---|---|---|
| `/deliveries/verify` | POST | fleetlogix-verificar-entrega | App móvil marca una entrega como completada |
| `/vehicles/eta` | POST / GET | fleetlogix-calcular-eta | App móvil o job programado consulta ETA |
| `/vehicles/location` | POST | fleetlogix-alerta-desvio | App móvil envía actualización de GPS |

### 2.2 S3 — históricos organizados por fecha

S3 almacena el histórico crudo de eventos (payloads de entregas, posiciones GPS, resultados de cada corrida) en el bucket **fleetlogix-data**, particionado por fecha para que las consultas analíticas (y una futura carga a Snowflake, como en el Avance 3) solo lean las particiones necesarias en vez de escanear todo el histórico.

El script `aws_setup.py` crea estas cuatro carpetas lógicas y define una regla de **lifecycle** que mueve automáticamente los objetos de `raw-data/` a la clase de almacenamiento **Glacier** (mucho más barata) luego de 90 días, ya que los datos crudos antiguos se consultan con muy poca frecuencia pero deben conservarse por razones de auditoría e histórico.

### 2.3 Lambda — procesamiento de cada entrega

Cada Lambda es un programa pequeño y con un solo propósito (principio de responsabilidad única), sin estado propio: toda la información que necesita persistir vive en DynamoDB, S3 o RDS. AWS ejecuta el código solo cuando llega un evento (petición HTTP, mensaje de un stream, disparador programado) y cobra por milisegundos de ejecución, no por servidores encendidos 24/7 — ideal para una carga de trabajo tan intermitente como confirmaciones de entrega o lecturas de GPS.

---

## 3. Recursos creados en lambda_functions.py y aws_setup.py

### 3.1 lambda_functions.py — 3 funciones Lambda

| Función | Recurso(s) AWS que usa | Funcionalidad |
|---|---|---|
| lambda_verificar_entrega | DynamoDB (deliveries_status) | Recibe delivery_id, consulta el estado de la entrega y responde si ya fue completada (status = 'delivered'). |
| lambda_calcular_eta | DynamoDB (vehicle_tracking) | Calcula distancia (Haversine simplificado) y ETA entre la ubicación actual y el destino, y guarda el snapshot de tracking. |
| lambda_alerta_desvio | DynamoDB (routes_waypoints, alerts_history), SNS | Compara la posición GPS actual contra los waypoints de la ruta; si la desviación supera 5 km, publica una alerta en SNS y la registra en DynamoDB. |

Ninguna de las tres crea infraestructura: **consumen** recursos ya existentes (tablas DynamoDB, topic SNS) creados por `aws_setup.py`. Por eso, al probarlas antes de correr `aws_setup.py`, fallan con `ResourceNotFoundException` — comportamiento esperado, no un error del código.

### 3.2 aws_setup.py — aprovisionamiento de infraestructura

| Función | Recurso(s) que crea | Funcionalidad |
|---|---|---|
| crear_rds_postgresql() | 1 instancia RDS PostgreSQL (db.t3.micro) | Aprovisiona la base relacional administrada: motor Postgres 15.4, 20 GB gp2, acceso público, backups automáticos de 7 días y ventana de mantenimiento. |
| crear_s3_bucket() | 1 bucket S3 + 4 carpetas + regla de lifecycle | Crea el bucket de históricos, la estructura raw-data/processed-data/backups/logs, y la transición a Glacier a los 90 días. |
| crear_tablas_dynamodb() | 4 tablas DynamoDB (modo on-demand) | deliveries_status, vehicle_tracking, routes_waypoints y alerts_history — el estado en tiempo real que consumen las 3 Lambda. |
| configurar_backups_automaticos() | 1 snapshot manual de RDS | Complementa los backups automáticos ya definidos en create_db_instance con un snapshot inicial de referencia. |
| migrar_datos_postgresql() | 1 archivo migrate_to_rds.sh (no un recurso AWS) | Genera el script bash con los comandos pg_dump / psql para migrar la base local a RDS (ver detalle en la sección 4). |
| crear_rol_iam_lambda() | 1 rol IAM (FleetLogixLambdaRole) + 4 políticas administradas | Rol que las 3 funciones Lambda asumen para poder invocar DynamoDB, S3, SNS y escribir logs en CloudWatch. |

En conjunto, `aws_setup.py` es el script de **infraestructura** (qué existe) y `lambda_functions.py` es el código de **negocio** (qué hace cada petición) — separación que facilita desplegar la infraestructura una sola vez y actualizar la lógica de las funciones de forma independiente y frecuente.

---

## 4. Funciones en detalle

### 4.1 b. Función Lambda verificar-entrega

**Trigger:** API Gateway, `POST /deliveries/verify`. La app móvil del conductor la invoca cada vez que se marca una entrega como completada, o cuando la propia app quiere confirmar el estado actual de un pedido.

**Entrada:** un JSON con `delivery_id` (obligatorio) y `tracking_number` (informativo). Antes de tocar la base de datos, la función valida que `delivery_id` venga presente y devuelve `400 Bad Request` si falta — evita una consulta innecesaria a DynamoDB por un request mal formado.

**Lógica:** hace un `get_item` sobre la tabla **deliveries_status** de DynamoDB usando `delivery_id` como clave de partición. DynamoDB es la elección correcta aquí porque es exactamente el patrón de acceso que optimiza: *"dame el ítem completo que corresponde a esta clave"*, con latencia de un dígito de milisegundos sin importar cuántas entregas existan en la tabla — muy distinto a un `SELECT ... WHERE delivery_id = ...` contra la tabla `deliveries` de PostgreSQL, que aunque también sería rápida con un índice, implica mantener una conexión abierta a una base relacional para una consulta de un solo campo, algo costoso de escalar cuando cientos de conductores golpean el endpoint simultáneamente.

- **Si el ítem existe:** responde 200 con `is_completed` (True si `status == 'delivered'`), el `tracking_number`, el `status` textual y la fecha/hora de entrega.
- **Si no existe:** responde 404 — típicamente porque el pedido aún no fue sincronizado desde el pipeline operacional hacia DynamoDB, o porque el `delivery_id` es inválido.
- **Ante cualquier excepción** (por ejemplo, falta de permisos IAM o la tabla inexistente): responde 500 con el mensaje de error — es justamente el error `ResourceNotFoundException` que la guía muestra al probar la función antes de correr `aws_setup.py`.

**Permisos que requiere:** únicamente `dynamodb:GetItem` sobre la tabla `deliveries_status` — la guía adjunta la política administrada `AmazonDynamoDBFullAccess` por simplicidad, pero en la sección 9 se documenta una alternativa de menor privilegio.

**Por qué importa para el negocio:** es la función que le da a la app del conductor una respuesta inmediata ("sí, tu entrega quedó registrada") sin depender del pipeline ETL nocturno hacia Snowflake del Avance 3, que corre una vez al día y no sirve para confirmar en el momento.

### 4.2 c. Función migrar_datos_postgresql()

A diferencia de las demás funciones de `aws_setup.py`, **esta función no llama a la API de AWS ni ejecuta ninguna migración por sí misma**: solo genera, en disco local, un archivo de texto (`migrate_to_rds.sh`) con los comandos que el usuario debe correr manualmente desde su terminal. Es un generador de script, no un migrador automático.

El script generado sigue el patrón clásico de **dump & restore** para una migración inicial (offline) de PostgreSQL:

```
1) pg_dump -h localhost -U $LOCAL_USER -d $LOCAL_DB -f fleetlogix_dump.sql
   -> Exporta toda la base local (schemas, tablas, datos, constraints) a un archivo .sql

2) psql -h $RDS_ENDPOINT -U $RDS_USER -c "CREATE DATABASE $RDS_DB;"
   -> Crea la base de datos vacía dentro de la instancia RDS ya aprovisionada

3) psql -h $RDS_ENDPOINT -U $RDS_USER -d $RDS_DB -f fleetlogix_dump.sql
   -> Reproduce el dump completo dentro de RDS, tabla por tabla
```

---

## 5. Procesar información: triggers y automatización

Cada una de las 3 funciones Lambda se activa con un disparador distinto, según qué tan "en tiempo real" necesita ser su respuesta:

| Lambda | Trigger propuesto | Frecuencia / razón |
|---|---|---|
| fleetlogix-verificar-entrega | API Gateway (POST /deliveries/verify) | Bajo demanda, cada vez que la app móvil marca una entrega — respuesta síncrona inmediata. |
| fleetlogix-calcular-eta | Amazon EventBridge (regla programada cada 5 min) | No necesita ser instantáneo; recalcular cada 5 min para todos los vehículos en ruta equilibra frescura del dato contra costo/carga. |
| fleetlogix-alerta-desvio | Amazon Kinesis Data Streams (stream de GPS) | Debe ser lo más cercano a tiempo real posible — un desvío se detecta con cada actualización de ubicación entrante, no en un batch programado. |

Esta combinación (petición síncrona + polling programado + streaming) es intencional: no todo necesita el mismo nivel de "tiempo real", y usar Kinesis solo para el caso que realmente lo exige (desvíos, por seguridad y costo operativo) evita sobre-diseñar los otros dos flujos.

---

## 6. Bases de datos en la nube

### 6.1 RDS — PostgreSQL administrado

La base operacional de FleetLogix (las 6 tablas del Avance 1-2: vehicles, drivers, routes, trips, deliveries, maintenance) se migra de la instalación local a **RDS PostgreSQL 15.4**, clase `db.t3.micro` (elegible para free tier), 20 GB de almacenamiento `gp2`. Sigue siendo el origen de verdad relacional que alimenta el pipeline ETL hacia Snowflake documentado en el Avance 3 — eso no cambia, solo cambia dónde vive físicamente la base.

### 6.2 DynamoDB — estado actual en tiempo real

Cuatro tablas, todas en modo **on-demand** (`PAY_PER_REQUEST`, sin necesidad de aprovisionar capacidad de lectura/escritura de antemano — apropiado porque el tráfico de una app de conductores es impredecible e intermitente, con picos en horas de reparto):

| Tabla | Clave (Partition / Sort) | Qué guarda |
|---|---|---|
| deliveries_status | delivery_id (HASH) | Estado actual de cada entrega (pending / delivered / etc.) |
| vehicle_tracking | vehicle_id (HASH) / timestamp (RANGE) | Historial reciente de posición GPS y ETA por vehículo |
| routes_waypoints | route_id (HASH) | Lista de waypoints esperados de cada ruta, para detectar desvíos |
| alerts_history | vehicle_id (HASH) / timestamp (RANGE) | Registro de alertas de desvío generadas |

DynamoDB complementa, no reemplaza, a RDS: RDS es fuerte en consultas relacionales complejas (joins, agregaciones, reportes como los del Avance 2), mientras que DynamoDB es fuerte en lecturas/escrituras de un solo ítem por clave a cualquier escala, que es exactamente el patrón de "¿cuál es el estado *ahora mismo* de X?" que necesitan las 3 Lambda.

### 6.3 Backups automáticos

- **RDS:** `BackupRetentionPeriod=7` (backups automáticos diarios, retenidos 7 días) más un snapshot manual inicial (`configurar_backups_automaticos()`) como punto de restauración de referencia antes de empezar a operar.

---

## 7. Pipeline de datos en tiempo real

### 7.1 Flujo básico

**App móvil → API Gateway → Lambda → Base de datos**, con S3 recibiendo una copia del evento crudo para histórico:

1. El conductor marca una entrega como completada (o la app reporta GPS) → petición HTTPS a API Gateway.
2. API Gateway valida el request, aplica throttling/autenticación y lo enruta a la Lambda correspondiente según el recurso invocado (tabla de la sección 2.1).
3. La Lambda procesa el evento: consulta o escribe en DynamoDB (estado actual) y, en paralelo, escribe una copia del evento crudo en S3 bajo la partición del día (`raw-data/year=/month=/day=`) para trazabilidad e ingestión futura al Data Warehouse.
4. La Lambda responde a la app móvil (síncrono) o publica una alerta vía SNS (caso de desvíos, asíncrono).

### 7.2 Eventos importantes a persistir

Además del estado "actual" en DynamoDB, conviene registrar el **evento** en sí (no solo el último valor) para poder reconstruir el historial y alimentar el pipeline ETL del Avance 3: entrega completada, entrega retrasada (`delay_minutes` por encima del umbral de puntualidad ya usado en el modelo dimensional), y alerta de desvío de ruta. Estos tres son justamente los que ya se guardan en `alerts_history` y, vía S3, en `raw-data/`.

### 7.3 Funcionalidad en tiempo real implementada (diseño)

La funcionalidad de menor latencia del sistema es la **detección de desvíos de ruta** (`fleetlogix-alerta-desvio`): al recibir cada actualización de GPS por Kinesis, la Lambda compara la posición contra los waypoints esperados y, si la distancia supera el umbral de 5 km, publica inmediatamente una alerta por SNS (email/SMS al equipo de operaciones).

---

## 8. Monitoreo y observabilidad (CloudWatch)

CloudWatch recibe métricas y logs de todos los servicios automáticamente; el trabajo de diseño está en elegir **qué** vigilar y **quién se entera** cuándo algo sale mal.

### 8.1 Métricas clave por servicio

| Servicio | Métricas a monitorear | Por qué |
|---|---|---|
| Lambda | Errors, Duration, Throttles, ConcurrentExecutions | Errors detecta fallas de lógica o de permisos IAM; Throttles indica que se está pegando contra el límite de concurrencia; Duration ayuda a detectar código lento antes de que impacte el costo o la UX. |
| API Gateway | Count, 4XXError, 5XXError, Latency, IntegrationLatency | 4XXError sostenido sugiere requests mal formados desde la app móvil; 5XXError apunta a fallas en las Lambda; Latency/IntegrationLatency separan tiempo de red vs. tiempo de procesamiento. |
| DynamoDB | ThrottledRequests, ConsumedRead/WriteCapacityUnits, SuccessfulRequestLatency | En modo on-demand el throttling debería ser raro; si aparece, indica un pico de tráfico que amerita revisión (ej. muchos conductores reportando GPS al mismo tiempo). |
| RDS | CPUUtilization, FreeStorageSpace, FreeableMemory, DatabaseConnections | FreeStorageSpace bajo puede tumbar la base; DatabaseConnections alto puede indicar conexiones no cerradas desde el pipeline ETL; CPU/memoria anticipan si hace falta subir de clase de instancia. |
| S3 | BucketSizeBytes, NumberOfObjects, 4xxErrors | Controla el crecimiento de costo del histórico y detecta fallos de escritura desde las Lambda. |
| SNS | NumberOfNotificationsFailed | Confirma que las alertas de desvío realmente le llegan al equipo de operaciones. |

### 8.2 Alertas automáticas

Se configurarían **CloudWatch Alarms** que, al superar un umbral, publican en un topic SNS dedicado a notificaciones operativas (distinto del topic de alertas de desvío de negocio), con email/SMS al equipo responsable:

- `Lambda Errors > 5 en 5 min` (cualquiera de las 3 funciones) → alarma alta prioridad, ya que afecta directamente la experiencia del conductor o la seguridad de la flota.
- `API Gateway 5XXError rate > 5%` sostenido en 5 min → posible caída del backend.
- `RDS FreeStorageSpace < 2 GB` → alarma antes de quedarse sin espacio.
- `RDS CPUUtilization > 80%` sostenido 10 min → posible necesidad de escalar la instancia.
- `DynamoDB ThrottledRequests > 0` → señal temprana de que el modo on-demand necesita revisión.
- Un **dashboard** de CloudWatch consolidando estas métricas en una sola vista para el equipo de operaciones, y **CloudWatch Logs Insights** para poder consultar rápidamente los logs de las 3 Lambda ante un incidente puntual.

---

## 9. IAM, encriptación y costos

### 9.1 Usuarios y roles con permisos limitados

La guía del avance ya evita usar la cuenta **root** para trabajar día a día: pide crear un Access Key desde "Security credentials" de un usuario IAM para configurar AWS CLI. Sin embargo, el rol que `crear_rol_iam_lambda()` arma para las funciones Lambda usa políticas administradas de alcance muy amplio (`AmazonDynamoDBFullAccess`, `AmazonS3FullAccess`, `AmazonSNSFullAccess`), que dan permiso sobre **todas** las tablas DynamoDB, **todos** los buckets S3 y **todos** los topics SNS de la cuenta.

De la misma forma, el acceso a S3 se limitaría al ARN del bucket `fleetlogix-data` (y su prefijo `raw-data/*`) en vez de `Resource: "*"`, y el acceso a SNS se limitaría al ARN puntual del topic `fleetlogix-alerts`. También conviene **un rol IAM por función** en vez de un único rol compartido: `fleetlogix-verificar-entrega` solo necesita `GetItem` sobre una tabla, no `PutItem` sobre las cuatro.

### 9.2 Encriptación

- **RDS:** activar `StorageEncrypted=True` al crear la instancia (no está seteado en el script actual) para cifrar en reposo con una clave KMS, y forzar SSL/TLS en las conexiones (`rds.force_ssl=1` en el parameter group) para cifrar en tránsito.
- **S3:** activar cifrado por defecto (SSE-S3 o SSE-KMS) a nivel de bucket, para que todo objeto nuevo quede cifrado en reposo sin depender de que cada `put_object` lo especifique.
- **DynamoDB:** el cifrado en reposo está activado por defecto (clave administrada por AWS); para mayor control se puede optar por una clave KMS administrada por el cliente.
- **Secretos** (contraseñas de RDS, credenciales): en vez de strings hardcodeados como `MasterUserPassword='FleetLogix2024!'` en el script, usar **AWS Secrets Manager** para generar y rotar la contraseña automáticamente.

### 9.3 Estimación de costos mensuales

Estimación conservadora para un volumen de uso bajo/moderado (etapa de aprendizaje/demo del PI, sin considerar los créditos de Free Tier de los primeros 12 meses, que reducirían varios de estos ítems a $0):

| Servicio | Supuesto de uso | Costo estimado / mes |
|---|---|---|
| RDS db.t3.micro + 20 GB gp2 | Single-AZ, encendida 24/7, backups 7 días | $14.70 |
| S3 Standard + lifecycle a Glacier | ~5 GB de históricos nuevos por mes | $0.50 |
| Lambda | Dentro del Free Tier (1M req + 400.000 GB-s/mes) | $0.00 |
| API Gateway (REST) | < 100.000 llamadas/mes | $0.35 |
| DynamoDB (on-demand) | Tráfico bajo, pocas escrituras/lecturas por minuto | $2.50 |
| SNS | Alertas de desvío + notificaciones operativas | $0.10 |
| CloudWatch | ~8 alarmas + logs de las 3 Lambda | $1.30 |
| Transferencia de datos saliente | Volumen bajo | $0.50 |
| **Total estimado** | | **≈ $19.95 / mes** |

---

## 10. Extra credit — despliegue de API Gateway

No fue ejecutado en AWS real (fuera del alcance evaluado de este avance), pero el diseño quedaría así si se implementara:

- Crear un API REST nuevo en API Gateway (`fleetlogix-api`).
- Crear 3 recursos: `/deliveries/verify`, `/vehicles/eta`, `/vehicles/location`.
- Conectar cada recurso, con el método HTTP correspondiente, a su Lambda vía integración Lambda Proxy (tabla de la sección 2.1), y darle a API Gateway permiso de invocación sobre cada función (`lambda:InvokeFunction`).
- Deploy del API a un stage (ej. `prod`), lo que genera automáticamente la URL invocable: `https://{api-id}.execute-api.us-east-1.amazonaws.com/prod/`.
- Distribuir esa URL base a la app móvil de los conductores como configuración de entorno (nunca hardcodeada en el binario, para poder rotarla sin republicar la app).

---

## 11. Conclusiones

La arquitectura propuesta resuelve los tres objetivos del avance con servicios administrados y sin servidor: **recibir** datos de forma centralizada y segura (API Gateway), **guardar** el histórico completo de forma económica y organizada (S3 con lifecycle), y **procesar** cada entrega con lógica simple y aislada (3 Lambda). La separación entre lo relacional (RDS, para análisis y el Data Warehouse del Avance 3) y lo operacional de baja latencia (DynamoDB, para el estado "ahora mismo") es la decisión de diseño central del avance, y se documentaron explícitamente las brechas actuales del código de referencia.