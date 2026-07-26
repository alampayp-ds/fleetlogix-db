"""
FleetLogix - Pipeline ETL Automático
Extrae de PostgreSQL, Transforma y Carga en Snowflake
Ejecución diaria automatizada
"""

import psycopg2
import snowflake.connector
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
import logging
import schedule
import time
import json
from typing import Dict, List, Tuple

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('etl_pipeline.log'),
        logging.StreamHandler()
    ]
)

# Configuración de conexiones
# ⚠️ COMPLETAR CON TUS CREDENCIALES REALES ANTES DE CORRER
POSTGRES_CONFIG = {
    'host': 'localhost',
    'database': 'fleetlogix',
    'user': 'your_user',
    'password': 'your_password',
    'port': 5432
}

SNOWFLAKE_CONFIG = {
    'user': 'your_user',
    'password': 'your_password',
    'account': 'your_account',
    'warehouse': 'FLEETLOGIX_WH',
    'database': 'FLEETLOGIX_DW',
    'schema': 'ANALYTICS'
}

class FleetLogixETL:
    def __init__(self):
        self.pg_conn = None
        self.sf_conn = None
        self.batch_id = int(datetime.now().timestamp())
        self.metrics = {
            'records_extracted': 0,
            'records_transformed': 0,
            'records_loaded': 0,
            'errors': 0
        }
    
    def connect_databases(self):
        """Establecer conexiones con PostgreSQL y Snowflake"""
        try:
            # PostgreSQL
            self.pg_conn = psycopg2.connect(**POSTGRES_CONFIG)
            logging.info(" Conectado a PostgreSQL")
            
            # Snowflake
            self.sf_conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
            logging.info(" Conectado a Snowflake")
            
            return True
        except Exception as e:
            logging.error(f" Error en conexión: {e}")
            return False
    
    def extract_daily_data(self) -> pd.DataFrame:
        """Extraer datos del día anterior de PostgreSQL"""
        logging.info(" Iniciando extracción de datos...")
        
        query = """
            SELECT
                d.delivery_id,
                d.trip_id,
                d.tracking_number,
                d.customer_name,
                d.package_weight_kg,
                d.scheduled_datetime,
                d.delivered_datetime,
                d.recipient_signature,
                d.delivery_status,
                t.vehicle_id,
                t.driver_id,
                t.route_id,
                t.departure_datetime,
                t.arrival_datetime,
                t.fuel_consumed_liters,
                r.distance_km,
                r.destination_city,
                r.toll_cost,
                dr.employee_code AS driver_employee_code,
                dr.first_name AS driver_first_name,
                dr.last_name AS driver_last_name,
                dr.license_number AS driver_license_number,
                dr.license_expiry AS driver_license_expiry,
                dr.phone AS driver_phone,
                dr.hire_date AS driver_hire_date,
                dr.status AS driver_status,
                v.license_plate AS vehicle_license_plate,
                v.vehicle_type AS vehicle_type,
                v.capacity_kg AS vehicle_capacity_kg,
                v.fuel_type AS vehicle_fuel_type,
                v.acquisition_date AS vehicle_acquisition_date,
                v.status AS vehicle_status
            FROM deliveries d
            JOIN trips t ON t.trip_id = d.trip_id
            JOIN routes r ON r.route_id = t.route_id
            JOIN drivers dr ON dr.driver_id = t.driver_id
            JOIN vehicles v ON v.vehicle_id = t.vehicle_id
            WHERE d.delivered_datetime::date = CURRENT_DATE - INTERVAL '1 day'
              AND d.delivery_status = 'delivered'
        """
        
        try:
            df = pd.read_sql(query, self.pg_conn)
            self.metrics['records_extracted'] = len(df)
            logging.info(f" Extraídos {len(df)} registros")
            return df
        except Exception as e:
            logging.error(f" Error en extracción: {e}")
            self.metrics['errors'] += 1
            return pd.DataFrame()
    
    def transform_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Transformar datos para el modelo dimensional"""
        logging.info(" Iniciando transformación de datos...")
        
        try:
            # Calcular métricas
            df['delivery_time_minutes'] = (
                (pd.to_datetime(df['delivered_datetime']) - 
                 pd.to_datetime(df['scheduled_datetime'])).dt.total_seconds() / 60
            ).round(2)
            
            df['delay_minutes'] = df['delivery_time_minutes'].apply(
                lambda x: max(0, x) if x > 0 else 0
            )
            
            df['is_on_time'] = df['delay_minutes'] <= 30
            
            # Calcular entregas por hora
            df['trip_duration_hours'] = (
                (pd.to_datetime(df['arrival_datetime']) - 
                 pd.to_datetime(df['departure_datetime'])).dt.total_seconds() / 3600
            ).round(2)
            
            # Agrupar entregas por trip para calcular entregas/hora
            deliveries_per_trip = df.groupby('trip_id').size()
            df['deliveries_in_trip'] = df['trip_id'].map(deliveries_per_trip)
            df['deliveries_per_hour'] = (
                df['deliveries_in_trip'] / df['trip_duration_hours']
            ).round(2)
            
            # Eficiencia de combustible
            df['fuel_efficiency_km_per_liter'] = (
                df['distance_km'] / df['fuel_consumed_liters']
            ).round(2)
            
            # Costo estimado por entrega
            df['cost_per_delivery'] = (
                (df['fuel_consumed_liters'] * 5000 + df['toll_cost']) / 
                df['deliveries_in_trip']
            ).round(2)
            
            # Revenue estimado (ejemplo: $20,000 base + $500 por kg)
            df['revenue_per_delivery'] = (20000 + df['package_weight_kg'] * 500).round(2)
            
            # Validaciones de calidad
            # No permitir tiempos negativos
            df = df[df['delivery_time_minutes'] >= 0]
            
            # No permitir pesos fuera de rango
            df = df[(df['package_weight_kg'] > 0) & (df['package_weight_kg'] < 10000)]
            
            # Manejar cambios históricos (SCD Type 2): preparar atributos de conductor y
            # vehículo que se van a comparar contra la versión vigente en Snowflake.
            # (la comparación y el UPDATE/INSERT real ocurren en load_dimensions,
            # porque ahí es donde tenemos acceso a lo que ya está guardado)
            df['driver_full_name'] = df['driver_first_name'] + ' ' + df['driver_last_name']
            df['driver_experience_months'] = (
                (pd.Timestamp.now() - pd.to_datetime(df['driver_hire_date'])).dt.days / 30
            ).round().astype(int)
            df['vehicle_age_months'] = (
                (pd.Timestamp.now() - pd.to_datetime(df['vehicle_acquisition_date'])).dt.days / 30
            ).round().astype(int)

            # Categoría de desempeño simple: % de puntualidad del conductor en este lote
            driver_on_time_rate = df.groupby('driver_id')['is_on_time'].mean()
            df['driver_performance_category'] = df['driver_id'].map(driver_on_time_rate).apply(
                lambda r: 'Alto' if r >= 0.9 else ('Medio' if r >= 0.7 else 'Bajo')
            )

            # Normalizar fechas a date() de Python (se comparan más fácil contra Snowflake)
            df['driver_hire_date'] = pd.to_datetime(df['driver_hire_date']).dt.date
            df['driver_license_expiry'] = pd.to_datetime(df['driver_license_expiry']).dt.date
            df['vehicle_acquisition_date'] = pd.to_datetime(df['vehicle_acquisition_date']).dt.date

            df['valid_from'] = pd.to_datetime(df['scheduled_datetime']).dt.date
            df['valid_to'] = date(9999, 12, 31)  # date() de Python: pd.Timestamp no soporta años > ~2262
            df['is_current'] = True
            
            self.metrics['records_transformed'] = len(df)
            logging.info(f" Transformados {len(df)} registros")
            
            return df
            
        except Exception as e:
            logging.error(f" Error en transformación: {e}")
            self.metrics['errors'] += 1
            return pd.DataFrame()
    
    def load_dimensions(self, df: pd.DataFrame):
        """Cargar o actualizar dimensiones en Snowflake"""
        logging.info(" Cargando dimensiones...")
        
        cursor = self.sf_conn.cursor()
        
        try:
            # Cargar dim_customer (nuevos clientes) — en lote, no uno por uno
            self._load_customers_bulk(cursor, df)
            
            # Actualizar dimensiones con manejo de cambios históricos (SCD Type 2)
            self._update_scd2_driver(cursor, df)
            self._update_scd2_vehicle(cursor, df)
            
            self.sf_conn.commit()
            logging.info(" Dimensiones actualizadas")
            
        except Exception as e:
            logging.error(f" Error cargando dimensiones: {e}")
            self.sf_conn.rollback()
            self.metrics['errors'] += 1
    
    def _load_customers_bulk(self, cursor, df: pd.DataFrame):
        """Carga clientes nuevos en dim_customer en un solo MERGE, en vez de uno por
        cliente. Con datos sintéticos casi todos los customer_name son distintos entre
        sí, así que loopear fila por fila significaba cientos de round-trips a Snowflake
        (uno de los cuellos de botella más comunes en pipelines ETL mal optimizados)."""
        customers = df[['customer_name', 'destination_city']].drop_duplicates(subset=['customer_name'])
        if customers.empty:
            return
        
        cursor.execute("""
            CREATE OR REPLACE TEMPORARY TABLE tmp_new_customers (
                customer_name VARCHAR(200),
                destination_city VARCHAR(100)
            )
        """)
        
        rows = list(customers.itertuples(index=False, name=None))
        cursor.executemany(
            "INSERT INTO tmp_new_customers (customer_name, destination_city) VALUES (%s, %s)",
            rows
        )
        
        cursor.execute("""
            MERGE INTO dim_customer c
            USING (
                SELECT
                    t.customer_name,
                    t.destination_city,
                    ROW_NUMBER() OVER (ORDER BY t.customer_name)
                        + (SELECT COALESCE(MAX(customer_key), 0) FROM dim_customer) AS new_key
                FROM tmp_new_customers t
            ) s
            ON c.customer_name = s.customer_name
            WHEN NOT MATCHED THEN
                INSERT (customer_key, customer_name, customer_type, city,
                       first_delivery_date, total_deliveries, customer_category)
                VALUES (s.new_key, s.customer_name, 'Individual', s.destination_city,
                       CURRENT_DATE(), 0, 'Regular')
        """)
    
    def _update_scd2_driver(self, cursor, df: pd.DataFrame):
        """SCD Type 2 para dim_driver, en lote: sube todos los conductores del día a una
        tabla temporal, detecta en una sola consulta cuáles son nuevos o cambiaron, y
        hace un UPDATE + INSERT masivo — en vez de 2 consultas por cada conductor."""
        drivers = df[['driver_id', 'driver_employee_code', 'driver_full_name',
                      'driver_license_number', 'driver_license_expiry', 'driver_phone',
                      'driver_hire_date', 'driver_experience_months', 'driver_status',
                      'driver_performance_category']].drop_duplicates(subset=['driver_id'])
        if drivers.empty:
            return
        
        cursor.execute("""
            CREATE OR REPLACE TEMPORARY TABLE tmp_drivers (
                driver_id INT, employee_code VARCHAR(20), full_name VARCHAR(200),
                license_number VARCHAR(50), license_expiry DATE, phone VARCHAR(20),
                hire_date DATE, experience_months INT, status VARCHAR(20),
                performance_category VARCHAR(20)
            )
        """)
        rows = [
            (int(r.driver_id), r.driver_employee_code, r.driver_full_name,
             r.driver_license_number, r.driver_license_expiry, r.driver_phone,
             r.driver_hire_date, int(r.driver_experience_months), r.driver_status,
             r.driver_performance_category)
            for r in drivers.itertuples(index=False)
        ]
        cursor.executemany("""
            INSERT INTO tmp_drivers (driver_id, employee_code, full_name, license_number,
                license_expiry, phone, hire_date, experience_months, status, performance_category)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, rows)
        
        # Conductores nuevos o con algún atributo distinto a su versión vigente
        cursor.execute("""
            CREATE OR REPLACE TEMPORARY TABLE tmp_drivers_changed AS
            SELECT t.*, d.driver_key AS old_driver_key
            FROM tmp_drivers t
            LEFT JOIN dim_driver d ON d.driver_id = t.driver_id AND d.is_current = TRUE
            WHERE d.driver_id IS NULL
               OR d.employee_code != t.employee_code
               OR d.full_name != t.full_name
               OR d.license_number != t.license_number
               OR d.license_expiry != t.license_expiry
               OR d.phone != t.phone
               OR d.hire_date != t.hire_date
               OR d.experience_months != t.experience_months
               OR d.status != t.status
               OR d.performance_category != t.performance_category
        """)
        
        # Cerrar las versiones viejas de los que cambiaron
        cursor.execute("""
            UPDATE dim_driver
            SET valid_to = CURRENT_DATE() - 1, is_current = FALSE
            WHERE driver_key IN (
                SELECT old_driver_key FROM tmp_drivers_changed WHERE old_driver_key IS NOT NULL
            )
        """)
        
        # Insertar la versión nueva de todos los que son nuevos o cambiaron
        cursor.execute("""
            INSERT INTO dim_driver (
                driver_key, driver_id, employee_code, full_name, license_number,
                license_expiry, phone, hire_date, experience_months, status,
                performance_category, valid_from, valid_to, is_current
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY driver_id) + (SELECT COALESCE(MAX(driver_key), 0) FROM dim_driver),
                driver_id, employee_code, full_name, license_number, license_expiry, phone,
                hire_date, experience_months, status, performance_category,
                CURRENT_DATE(), '9999-12-31', TRUE
            FROM tmp_drivers_changed
        """)
    
    def _update_scd2_vehicle(self, cursor, df: pd.DataFrame):
        """SCD Type 2 para dim_vehicle, en lote — mismo patrón que _update_scd2_driver."""
        vehicles = df[['vehicle_id', 'vehicle_license_plate', 'vehicle_type',
                       'vehicle_capacity_kg', 'vehicle_fuel_type', 'vehicle_acquisition_date',
                       'vehicle_age_months', 'vehicle_status']].drop_duplicates(subset=['vehicle_id'])
        if vehicles.empty:
            return
        
        cursor.execute("""
            CREATE OR REPLACE TEMPORARY TABLE tmp_vehicles (
                vehicle_id INT, license_plate VARCHAR(20), vehicle_type VARCHAR(50),
                capacity_kg DECIMAL(10,2), fuel_type VARCHAR(20), acquisition_date DATE,
                age_months INT, status VARCHAR(20)
            )
        """)
        rows = [
            (int(r.vehicle_id), r.vehicle_license_plate, r.vehicle_type,
             float(r.vehicle_capacity_kg), r.vehicle_fuel_type, r.vehicle_acquisition_date,
             int(r.vehicle_age_months), r.vehicle_status)
            for r in vehicles.itertuples(index=False)
        ]
        cursor.executemany("""
            INSERT INTO tmp_vehicles (vehicle_id, license_plate, vehicle_type, capacity_kg,
                fuel_type, acquisition_date, age_months, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, rows)
        
        cursor.execute("""
            CREATE OR REPLACE TEMPORARY TABLE tmp_vehicles_changed AS
            SELECT t.*, v.vehicle_key AS old_vehicle_key
            FROM tmp_vehicles t
            LEFT JOIN dim_vehicle v ON v.vehicle_id = t.vehicle_id AND v.is_current = TRUE
            WHERE v.vehicle_id IS NULL
               OR v.license_plate != t.license_plate
               OR v.vehicle_type != t.vehicle_type
               OR v.capacity_kg != t.capacity_kg
               OR v.fuel_type != t.fuel_type
               OR v.acquisition_date != t.acquisition_date
               OR v.age_months != t.age_months
               OR v.status != t.status
        """)
        
        cursor.execute("""
            UPDATE dim_vehicle
            SET valid_to = CURRENT_DATE() - 1, is_current = FALSE
            WHERE vehicle_key IN (
                SELECT old_vehicle_key FROM tmp_vehicles_changed WHERE old_vehicle_key IS NOT NULL
            )
        """)
        
        cursor.execute("""
            INSERT INTO dim_vehicle (
                vehicle_key, vehicle_id, license_plate, vehicle_type, capacity_kg,
                fuel_type, acquisition_date, age_months, status,
                valid_from, valid_to, is_current
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY vehicle_id) + (SELECT COALESCE(MAX(vehicle_key), 0) FROM dim_vehicle),
                vehicle_id, license_plate, vehicle_type, capacity_kg, fuel_type,
                acquisition_date, age_months, status,
                CURRENT_DATE(), '9999-12-31', TRUE
            FROM tmp_vehicles_changed
        """)
    
    def load_facts(self, df: pd.DataFrame):
        """Cargar hechos en Snowflake"""
        logging.info(" Cargando tabla de hechos...")
        
        cursor = self.sf_conn.cursor()
        
        try:
            # Preparar datos para inserción
            fact_data = []
            for _, row in df.iterrows():
                # Obtener keys de dimensiones
                date_key = int(pd.to_datetime(row['scheduled_datetime']).strftime('%Y%m%d'))
                scheduled_time_key = pd.to_datetime(row['scheduled_datetime']).hour * 100
                delivered_time_key = pd.to_datetime(row['delivered_datetime']).hour * 100
                
                fact_data.append((
                    date_key,
                    scheduled_time_key,
                    delivered_time_key,
                    row['vehicle_id'],  # Simplificado, debería buscar vehicle_key
                    row['driver_id'],   # Simplificado, debería buscar driver_key
                    row['route_id'],    # Simplificado, debería buscar route_key
                    1,  # customer_key placeholder
                    row['delivery_id'],
                    row['trip_id'],
                    row['tracking_number'],
                    row['package_weight_kg'],
                    row['distance_km'],
                    row['fuel_consumed_liters'],
                    row['delivery_time_minutes'],
                    row['delay_minutes'],
                    row['deliveries_per_hour'],
                    row['fuel_efficiency_km_per_liter'],
                    row['cost_per_delivery'],
                    row['revenue_per_delivery'],
                    row['is_on_time'],
                    False,  # is_damaged
                    row['recipient_signature'],
                    row['delivery_status'],
                    self.batch_id
                ))
            
            # Insertar en batch
            cursor.executemany("""
                INSERT INTO fact_deliveries (
                    date_key, scheduled_time_key, delivered_time_key,
                    vehicle_key, driver_key, route_key, customer_key,
                    delivery_id, trip_id, tracking_number,
                    package_weight_kg, distance_km, fuel_consumed_liters,
                    delivery_time_minutes, delay_minutes, deliveries_per_hour,
                    fuel_efficiency_km_per_liter, cost_per_delivery, revenue_per_delivery,
                    is_on_time, is_damaged, has_signature, delivery_status,
                    etl_batch_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, fact_data)
            
            self.sf_conn.commit()
            self.metrics['records_loaded'] = len(fact_data)
            logging.info(f" Cargados {len(fact_data)} registros en fact_deliveries")
            
        except Exception as e:
            logging.error(f" Error cargando hechos: {e}")
            self.sf_conn.rollback()
            self.metrics['errors'] += 1
    
    def run_etl(self):
        """Ejecutar pipeline ETL completo"""
        start_time = datetime.now()
        logging.info(f" Iniciando ETL - Batch ID: {self.batch_id}")
        
        try:
            # Conectar
            if not self.connect_databases():
                return
            
            # ETL
            df = self.extract_daily_data()
            if not df.empty:
                df_transformed = self.transform_data(df)
                if not df_transformed.empty:
                    self.load_dimensions(df_transformed)
                    self.load_facts(df_transformed)
            
            # Calcular totales para reportes
            self._calculate_daily_totals()
            
            # Cerrar conexiones
            self.close_connections()
            
            # Log final
            duration = (datetime.now() - start_time).total_seconds()
            logging.info(f" ETL completado en {duration:.2f} segundos")
            logging.info(f" Métricas: {json.dumps(self.metrics, indent=2)}")
            
        except Exception as e:
            logging.error(f" Error fatal en ETL: {e}")
            self.metrics['errors'] += 1
            self.close_connections()
    
    def _calculate_daily_totals(self):
        """Pre-calcular totales para reportes rápidos"""
        cursor = self.sf_conn.cursor()
        
        try:
            # Crear tabla de totales si no existe
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_totals (
                    batch_id INT PRIMARY KEY,
                    total_date DATE,
                    total_deliveries INT,
                    total_revenue DECIMAL(12,2),
                    total_cost DECIMAL(12,2),
                    avg_delivery_time_minutes DECIMAL(10,2),
                    on_time_percentage DECIMAL(5,2),
                    total_fuel_consumed_liters DECIMAL(12,2),
                    calculated_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
                )
            """)
            
            # Insertar totales del día, agregando desde fact_deliveries por batch_id
            cursor.execute("""
                INSERT INTO daily_totals (
                    batch_id, total_date, total_deliveries, total_revenue, total_cost,
                    avg_delivery_time_minutes, on_time_percentage, total_fuel_consumed_liters
                )
                SELECT
                    etl_batch_id,
                    CURRENT_DATE(),
                    COUNT(*),
                    SUM(revenue_per_delivery),
                    SUM(cost_per_delivery),
                    AVG(delivery_time_minutes),
                    ROUND(100.0 * SUM(CASE WHEN is_on_time THEN 1 ELSE 0 END) / COUNT(*), 2),
                    SUM(fuel_consumed_liters)
                FROM fact_deliveries
                WHERE etl_batch_id = %s
                GROUP BY etl_batch_id
            """, (self.batch_id,))
            
            self.sf_conn.commit()
            logging.info(" Totales diarios calculados")
            
        except Exception as e:
            logging.error(f" Error calculando totales: {e}")
    
    def close_connections(self):
        """Cerrar conexiones a bases de datos"""
        if self.pg_conn:
            self.pg_conn.close()
        if self.sf_conn:
            self.sf_conn.close()
        logging.info(" Conexiones cerradas")

def job():
    """Función para programar con schedule"""
    etl = FleetLogixETL()
    etl.run_etl()

def main():
    """Función principal - Automatización diaria"""
    logging.info(" Pipeline ETL FleetLogix iniciado")
    
    # Programar ejecución diaria a las 2:00 AM
    schedule.every().day.at("02:00").do(job)
    
    logging.info(" ETL programado para ejecutarse diariamente a las 2:00 AM")
    logging.info("Presiona Ctrl+C para detener")
    
    # Ejecutar una vez al inicio (para pruebas)
    job()
    
    # Loop infinito esperando la hora programada
    while True:
        schedule.run_pending()
        time.sleep(60)  # Verificar cada minuto

if __name__ == "__main__":
    main()