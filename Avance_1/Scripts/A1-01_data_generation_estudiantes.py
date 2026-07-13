"""
FleetLogix - Generación de Datos Sintéticos
Genera 505000+ registros respetando relaciones y reglas de negocio
"""

import psycopg2
from psycopg2.extras import execute_batch
import pandas as pd
import numpy as np
from faker import Faker
from datetime import datetime, timedelta
import random
import logging
from tqdm import tqdm
import json

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data_generation.log'),
        logging.StreamHandler()
    ]
)

# Configuración de conexión
from db_config import DB_CONFIG

# Inicializar Faker con semilla para reproducibilidad
fake = Faker('es_CO')  # Español Colombia (por Bogotá)
Faker.seed(42)
random.seed(42)
np.random.seed(42)

class DataGenerator:
    def __init__(self, db_config):
        self.db_config = db_config
        self.connection = None
        self.cursor = None
        self.cities = ['Bogotá', 'Medellín', 'Cali', 'Barranquilla', 'Cartagena']
        
        # Contadores para logging
        self.counters = {
            'vehicles': 0,
            'drivers': 0,
            'routes': 0,
            'trips': 0,
            'deliveries': 0,
            'maintenance': 0
        }
    
    def connect(self):
        """Establecer conexión con la base de datos"""
        try:
            self.connection = psycopg2.connect(**self.db_config)
            self.cursor = self.connection.cursor()
            logging.info(" Conexión exitosa a PostgreSQL")
            return True
        except Exception as e:
            logging.error(f" Error al conectar: {e}")
            return False
    
    def generate_vehicles(self, count=200):
        """Generar 200 vehículos con diferentes tipos y capacidades"""
        logging.info(f"Generando {count} vehículos...")
        
        vehicle_types = [
            ('Camión Grande', 5000, 'diesel', 0.3),
            ('Camión Mediano', 3000, 'diesel', 0.3),
            ('Van', 1500, 'gasolina', 0.3),
            ('Motocicleta', 50, 'gasolina', 0.1)
        ]
        
        vehicles = []
        for i in range(count):
            v_type, capacity, fuel, prob = random.choices(
                vehicle_types, 
                weights=[vt[3] for vt in vehicle_types]
            )[0]
            
            # Generar placa colombiana (ABC123)
            license_plate = f"{fake.random_uppercase_letter()}{fake.random_uppercase_letter()}{fake.random_uppercase_letter()}{random.randint(100,999)}"
            
            # Fecha de adquisición en los últimos 5 años
            acquisition_date = fake.date_between(start_date='-5y', end_date='-1m')
            
            # 90% activos, 10% en mantenimiento o inactivos
            status = random.choice(['active'] * 9 + ['maintenance'])
            
            vehicles.append((
                license_plate,
                v_type,
                capacity,
                fuel,
                acquisition_date,
                status
            ))
        
        # Insertar en batch
        query = """
            INSERT INTO vehicles (license_plate, vehicle_type, capacity_kg, fuel_type, acquisition_date, status)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        
        execute_batch(self.cursor, query, vehicles, page_size=100)
        self.connection.commit()
        self.counters['vehicles'] = count
        logging.info(f" {count} vehículos insertados")
    
    def generate_drivers(self, count=400):
        """Generar 400 conductores con datos realistas"""
        logging.info(f"Generando {count} conductores...")
        
        drivers = []
        license_types = ['C1', 'C2', 'C3', 'A2']  # Tipos de licencia Colombia
        
        for i in range(count):
            employee_code = f"EMP{str(i+1).zfill(4)}"
            first_name = fake.first_name()
            last_name = fake.last_name()
            
            # Licencia colombiana
            license_number = f"{random.randint(1000000000, 9999999999)}"
            license_type = random.choice(license_types)
            
            # Licencia válida por 3 años, algunas próximas a vencer
            license_expiry = fake.date_between(start_date='-1m', end_date='+3y')
            
            phone = f"3{random.randint(100000000, 999999999)}"  # Celular colombiano
            
            # Fecha de contratación en los últimos 5 años
            hire_date = fake.date_between(start_date='-5y', end_date='-1w')
            
            # 95% activos
            status = random.choice(['active'] * 19 + ['inactive'])
            
            drivers.append((
                employee_code,
                first_name,
                last_name,
                license_number,
                license_expiry,
                phone,
                hire_date,
                status
            ))
        
        query = """
            INSERT INTO drivers (employee_code, first_name, last_name, 
                               license_number, license_expiry, phone, 
                               hire_date, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        execute_batch(self.cursor, query, drivers, page_size=100)
        self.connection.commit()
        self.counters['drivers'] = count
        logging.info(f" {count} conductores insertados")
    
    def generate_routes(self, count=50):
        """Generar 50 rutas entre las 5 ciudades principales"""
        logging.info(f"Generando {count} rutas...")
        
        routes = []
        route_counter = 1
        
        # Generar rutas entre todas las combinaciones de ciudades
        for origin in self.cities:
            for destination in self.cities:
                if origin != destination:
                    # Múltiples rutas entre ciudades principales
                    num_routes = 3 if origin == 'Bogotá' or destination == 'Bogotá' else 2
                    
                    for i in range(num_routes):
                        route_code = f"R{str(route_counter).zfill(3)}"
                        
                        # Distancias aproximadas entre ciudades colombianas
                        base_distance = self._get_distance(origin, destination)
                        distance = base_distance + random.uniform(-50, 50)
                        
                        # Tiempo estimado (60-80 km/h promedio)
                        avg_speed = random.uniform(60, 80)
                        duration = distance / avg_speed
                        
                        # Peajes basados en distancia
                        toll_cost = int(distance / 100) * 15000  # 15k pesos por cada 100km
                        
                        routes.append((
                            route_code,
                            origin,
                            destination,
                            round(distance, 2),
                            round(duration, 2),
                            toll_cost
                        ))
                        
                        route_counter += 1
                        if route_counter > count:
                            break
                    
                    if route_counter > count:
                        break
            
            if route_counter > count:
                break
        
        # Ajustar para tener exactamente 50 rutas
        routes = routes[:count]
        
        query = """
            INSERT INTO routes (route_code, origin_city, destination_city, 
                              distance_km, estimated_duration_hours, toll_cost)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        
        execute_batch(self.cursor, query, routes, page_size=50)
        self.connection.commit()
        self.counters['routes'] = count
        logging.info(f" {count} rutas insertadas")
    
    def _get_distance(self, origin, destination):
        """Obtener distancia aproximada entre ciudades colombianas"""
        distances = {
            ('Bogotá', 'Medellín'): 440,
            ('Bogotá', 'Cali'): 460,
            ('Bogotá', 'Barranquilla'): 1000,
            ('Bogotá', 'Cartagena'): 1050,
            ('Medellín', 'Cali'): 420,
            ('Medellín', 'Barranquilla'): 640,
            ('Medellín', 'Cartagena'): 640,
            ('Cali', 'Barranquilla'): 1100,
            ('Cali', 'Cartagena'): 1100,
            ('Barranquilla', 'Cartagena'): 120
        }
        
        key = tuple(sorted([origin, destination]))
        return distances.get(key, 500)
    
    def generate_trips(self, count=100000):
        """Generar 100000 viajes en 2 años de operación"""
        logging.info(f"Generando {count} viajes...")
        
        # Obtener IDs válidos
        self.cursor.execute("SELECT vehicle_id, capacity_kg FROM vehicles WHERE status = 'active'")
        vehicles = self.cursor.fetchall()
        
        self.cursor.execute("SELECT driver_id FROM drivers WHERE status = 'active'")
        drivers = [d[0] for d in self.cursor.fetchall()]
        
        self.cursor.execute("SELECT route_id, distance_km, estimated_duration_hours FROM routes")
        routes = self.cursor.fetchall()
        
        # Fecha inicial: 2 años atrás
        start_date = datetime.now() - timedelta(days=730)
        
        trips = []
        current_date = start_date
        
        # Distribuir viajes a lo largo de 2 años
        for i in tqdm(range(count), desc="Generando trips"):
            vehicle_id, capacity = random.choice(vehicles)
            capacity = float(capacity)
            driver_id = random.choice(drivers)
            route_id, distance, est_duration = random.choice(routes)
            
            distance = float(distance)
            est_duration = float(est_duration)
            
            # Horario de salida (más viajes en horario laboral)
            hour = np.random.choice(
                range(24), 
                p=self._get_hourly_distribution()
            )
            departure = current_date.replace(hour=hour, minute=random.randint(0, 59))
            
            # Duración real con variación
            actual_duration = est_duration * random.uniform(0.8, 1.3)
            arrival = departure + timedelta(hours=actual_duration)
            
            # Consumo de combustible basado en distancia y tipo de vehículo
            fuel_consumed = distance * random.uniform(0.08, 0.15)  # 8-15L/100km
            
            # Peso total (40-90% de capacidad)
            total_weight = capacity * random.uniform(0.4, 0.9)
            
            # Estado (95% completados)
            if arrival < datetime.now():
                status = 'completed'
            else:
                status = 'in_progress'
            
            trips.append((
                vehicle_id,
                driver_id,
                route_id,
                departure,
                arrival if status == 'completed' else None,
                round(fuel_consumed, 2),
                round(total_weight, 2),
                status
            ))
            
            # Avanzar fecha (distribución uniforme)
            current_date += timedelta(minutes=int(1440 * 2 * 365 / count))
        
        # Insertar en batches
        query = """
            INSERT INTO trips (vehicle_id, driver_id, route_id, departure_datetime,
                             arrival_datetime, fuel_consumed_liters, total_weight_kg, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        batch_size = 1000
        for i in range(0, len(trips), batch_size):
            batch = trips[i:i+batch_size]
            execute_batch(self.cursor, query, batch, page_size=100)
            self.connection.commit()
            
            if i % 10000 == 0:
                logging.info(f"  Progreso: {i}/{count} trips insertados")
        
        self.counters['trips'] = count
        logging.info(f" {count} viajes insertados")
    
    def _get_hourly_distribution(self):
        """Distribución de probabilidad por hora del día"""
        # Más viajes en horario laboral
        probs = np.ones(24) * 0.02  # Base 2%
        probs[6:20] = 0.06  # 6am-8pm más actividad
        probs[8:12] = 0.08  # 8am-12pm pico mañana
        probs[14:18] = 0.07  # 2pm-6pm pico tarde
        return probs / probs.sum()
    
    def generate_deliveries(self, count=400000):
        """Generar 400000 entregas (promedio 4 por viaje)"""
        logging.info(f"Generando {count} entregas...")
        
        # Obtener todos los trips con su información
        self.cursor.execute("""
            SELECT trip_id, departure_datetime, arrival_datetime, total_weight_kg, destination_city
            FROM trips JOIN routes ON routes.route_id = trips.route_id
        """)
        trips_data = self.cursor.fetchall()
        
        deliveries = []
        delivery_counter = 0
        
        # Distribuir entregas entre los viajes
        for trip_id, departure, arrival, total_weight, city in tqdm(trips_data, desc="Generando deliveries"):
            # Número de entregas para este viaje (2-6, promedio 4)
            num_deliveries = np.random.choice([2, 3, 4, 5, 6], p=[0.1, 0.2, 0.4, 0.2, 0.1])
            
            # Peso por entrega
            weights = self._distribute_weight(float(total_weight), num_deliveries)
            #weights = self._distribute_weight(total_weight, num_deliveries)
            
            # Tiempo entre entregas
            if arrival:
                delivery_duration = (arrival - departure).total_seconds() / 3600
                time_per_delivery = delivery_duration / num_deliveries
            else:
                time_per_delivery = 0.5  # 30 minutos promedio
            
            for i in range(num_deliveries):
                tracking_number = f"FL{datetime.now().year}{str(delivery_counter+1).zfill(8)}"
                customer_name = fake.name()
                delivery_address = f"{fake.street_address()}, {city}"
                package_weight = float(weights[i])
                
                # Horario programado y real
                scheduled = departure + timedelta(hours=time_per_delivery * (i + 0.5))
                
                if arrival:
                    # 90% entregados a tiempo, 10% con retraso
                    if random.random() < 0.9:
                        delivered = scheduled + timedelta(minutes=random.randint(-30, 30))
                    else:
                        delivered = scheduled + timedelta(minutes=random.randint(60, 180))
                    
                    delivery_status = 'delivered'
                    signature = random.random() < 0.95  # 95% con firma
                else:
                    delivered = None
                    delivery_status = 'pending'
                    signature = False
                
                deliveries.append((
                    trip_id,
                    tracking_number,
                    customer_name,
                    delivery_address,
                    round(package_weight, 2),
                    scheduled,
                    delivered,
                    delivery_status,
                    signature
                ))
                
                delivery_counter += 1
                
                if delivery_counter > count:
                    break
            
            if delivery_counter > count:
                break
        
        # Insertar en batches
        query = """
            INSERT INTO deliveries (trip_id, tracking_number, customer_name,
                                  delivery_address, package_weight_kg, scheduled_datetime,
                                  delivered_datetime, delivery_status, recipient_signature)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        batch_size = 1000
        for i in range(0, len(deliveries), batch_size):
            batch = deliveries[i:i+batch_size]
            execute_batch(self.cursor, query, batch, page_size=100)
            self.connection.commit()
            
            if i % 50000 == 0:
                logging.info(f"  Progreso: {i}/{len(deliveries)} deliveries insertados")
        
        self.counters['deliveries'] = len(deliveries)
        logging.info(f" {len(deliveries)} entregas insertadas")
    
    def _distribute_weight(self, total_weight, num_packages):
        """Distribuir peso total entre paquetes de manera realista"""
        # Generar pesos aleatorios
        weights = np.random.exponential(scale=1.0, size=num_packages)
        # Normalizar para que sumen el total
        weights = weights / weights.sum() * total_weight * 0.95  # 95% del peso total
        # Mínimo 0.5kg por paquete
        weights = np.maximum(weights, 0.5)
        return weights
    
    def generate_maintenance(self, count=5000):
        """Generar 5000 registros de mantenimiento"""
        logging.info(f"Generando {count} registros de mantenimiento...")
        
        # Obtener información de vehículos y sus viajes
        self.cursor.execute("""
            SELECT vehicles.vehicle_id, vehicle_type, COUNT(trip_id), MIN(departure_datetime), MAX(departure_datetime) 
            FROM vehicles JOIN trips ON trips.vehicle_id = vehicles.vehicle_id
            GROUP BY vehicles.vehicle_id, vehicle_type
        """)
        vehicle_stats = self.cursor.fetchall()
        
        maintenance_types = [
            ('Cambio de aceite', 150000, 30),
            ('Revisión de frenos', 250000, 60),
            ('Cambio de llantas', 450000, 90),
            ('Mantenimiento general', 350000, 45),
            ('Revisión de motor', 500000, 60),
            ('Alineación y balanceo', 180000, 30)
        ]
        
        maintenance_records = []
        
        for vehicle_id, vehicle_type, trip_count, first_trip, last_trip in vehicle_stats:
            # Número de mantenimientos basado en viajes (cada ~20 viajes)
            num_maintenance = max(1, trip_count // 20)
            
            if first_trip and last_trip:
                # Distribuir mantenimientos en el período de operación
                operation_days = (last_trip - first_trip).days
                
                for i in range(min(num_maintenance, count - len(maintenance_records))):
                    # Fecha de mantenimiento distribuida
                    days_offset = int(operation_days * (i + 1) / (num_maintenance + 1))
                    maintenance_date = (first_trip + timedelta(days=days_offset)).date()
                    
                    # Tipo de mantenimiento
                    maint_type, base_cost, days_next = random.choice(maintenance_types)
                    
                    # Costo con variación
                    cost = base_cost * random.uniform(0.8, 1.2)
                    
                    # Descripción
                    description = f"{maint_type} programado para {maintenance_date.strftime('%Y-%m-%d')}"
                    
                    # Próximo mantenimiento
                    next_maintenance = maintenance_date + timedelta(days=days_next)
                    
                    # Técnico
                    performed_by = f"{fake.first_name()} {fake.last_name()}"
                    
                    maintenance_records.append((
                        vehicle_id,
                        maintenance_date,
                        maint_type,
                        description,
                        round(cost, 2),
                        next_maintenance,
                        performed_by
                    ))
                    
                    if len(maintenance_records) >= count:
                        break
        
        # Insertar en batch
        query = """
            INSERT INTO maintenance (vehicle_id, maintenance_date, maintenance_type,
                                   description, cost, next_maintenance_date, performed_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        
        execute_batch(self.cursor, query, maintenance_records[:count], page_size=100)
        self.connection.commit()
        self.counters['maintenance'] = min(len(maintenance_records), count)
        logging.info(f" {self.counters['maintenance']} mantenimientos insertados")
    
    def validate_data_quality(self):
        """Validar integridad y calidad de datos"""
        logging.info("\n VALIDANDO CALIDAD DE DATOS...")
        
        validations = {
            "Integridad referencial - Trips sin vehículo válido": """
                SELECT COUNT(*) FROM trips t 
                LEFT JOIN vehicles v ON t.vehicle_id = v.vehicle_id 
                WHERE v.vehicle_id IS NULL
            """,
            "Integridad referencial - Deliveries sin trip válido": """
                SELECT COUNT(*) FROM deliveries d 
                LEFT JOIN trips t ON d.trip_id = t.trip_id 
                WHERE t.trip_id IS NULL
            """,
            "Consistencia temporal - Trips con arrival < departure": """
                SELECT COUNT(*) FROM trips 
                WHERE arrival_datetime IS NOT NULL 
                AND arrival_datetime < departure_datetime
            """,
            "Consistencia de peso - Trips excediendo capacidad": """
                SELECT COUNT(*) FROM trips t 
                JOIN vehicles v ON t.vehicle_id = v.vehicle_id 
                WHERE t.total_weight_kg > v.capacity_kg
            """,
            "Entregas sin tracking number": """
                SELECT COUNT(*) FROM deliveries 
                WHERE tracking_number IS NULL OR tracking_number = ''
            """
        }
        
        all_valid = True
        for description, query in validations.items():
            self.cursor.execute(query)
            count = self.cursor.fetchone()[0]
            if count > 0:
                logging.warning(f"    {description}: {count} registros")
                all_valid = False
            else:
                logging.info(f"   {description}: OK")
        
        return all_valid
    
    def generate_summary_report(self):
        """Generar reporte resumen de datos generados"""
        logging.info("\n RESUMEN DE GENERACIÓN DE DATOS")
        logging.info("="*50)
        
        # Conteos finales
        tables = ['vehicles', 'drivers', 'routes', 'trips', 'deliveries', 'maintenance']
        total_records = 0
        
        for table in tables:
            self.cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = self.cursor.fetchone()[0]
            logging.info(f"  {table}: {count:,} registros")
            total_records += count
        
        logging.info(f"\n  TOTAL: {total_records:,} registros")
        
        # Estadísticas adicionales
        self.cursor.execute("""
            SELECT 
                AVG(delivery_count) as avg_deliveries_per_trip,
                MIN(delivery_count) as min_deliveries,
                MAX(delivery_count) as max_deliveries
            FROM (
                SELECT trip_id, COUNT(*) as delivery_count
                FROM deliveries
                GROUP BY trip_id
            ) as delivery_stats
        """)
        
        avg_del, min_del, max_del = self.cursor.fetchone()
        logging.info(f"\n  Entregas por viaje: AVG={avg_del:.1f}, MIN={min_del}, MAX={max_del}")
        
        # Guardar resumen en JSON
        summary = {
            'generation_date': datetime.now().isoformat(),
            'total_records': total_records,
            'table_counts': self.counters,
            'validations_passed': self.validate_data_quality()
        }
        
        with open('generation_summary.json', 'w') as f:
            json.dump(summary, f, indent=2)
        
        logging.info("\n Resumen guardado en generation_summary.json")
    
    def close(self):
        """Cerrar conexión"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        logging.info("\n Conexión cerrada")


def main():
    """Función principal"""
    print(" FLEETLOGIX - Generación de Datos Masivos")
    print("="*60)
    print("Objetivo: Generar 505000+ registros manteniendo integridad")
    print("="*60)
    
    generator = DataGenerator(DB_CONFIG)
    
    try:
        if not generator.connect():
            return
        
        # Generar datos en orden (respetando foreign keys)
        generator.generate_vehicles(200)
        generator.generate_drivers(400)
        generator.generate_routes(50)
        generator.generate_trips(100000)
        generator.generate_deliveries(400000)
        generator.generate_maintenance(5000)
        
        # Validar y generar reporte
        generator.generate_summary_report()
        
    except Exception as e:
        logging.error(f" Error durante la generación: {e}")
        generator.connection.rollback()
    finally:
        generator.close()


if __name__ == "__main__":
    main()