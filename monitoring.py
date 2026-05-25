import os
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.metrics._internal.aggregation import LastValueAggregation

# 1. Configuration Dynamique du Nom de Service
# On récupère le nom du conteneur via une variable d'environnement (ex: "executor" ou "database")
# Si elle n'est pas définie, on met "bookstore-service" par défaut.
SERVICE_IDENTIFIER = os.getenv("SERVICE_NAME", "bookstore-service")

resource = Resource.create(attributes={
    SERVICE_NAME: SERVICE_IDENTIFIER
})

# 2. Config Traces (HTTP sur le port 4318)
tracerProvider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(
    OTLPSpanExporter(endpoint="http://observability:4318/v1/traces")
)
tracerProvider.add_span_processor(processor)
trace.set_tracer_provider(tracerProvider)
tracer = trace.get_tracer(f"{SERVICE_IDENTIFIER}-tracer")

# 3. Config Metrics (HTTP sur le port 4318) - CORRIGÉ ICI POUR /v1/metrics
reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(endpoint="http://observability:4318/v1/metrics"),
    export_interval_millis=1000  # Intervalle de 1s demandé par l'énoncé
)
meterProvider = MeterProvider(resource=resource, metric_readers=[reader])
metrics.set_meter_provider(meterProvider)
meter = metrics.get_meter(f"{SERVICE_IDENTIFIER}-meter")

# ============================================================================== 
# LES 5 INSTRUMENTS REQUIS (2 EXEMPLES DE CHAQUE)
# ============================================================================== 

# --- 1. SPANS (Traces) ---
# (Ceux-ci s'utilisent directement via des blocs 'with tracer.start_as_current_span(...)')
# Exemple 1: tracer.start_as_current_span("run_2pc_process")
# Exemple 2: tracer.start_as_current_span("quorum_read_db")

# --- 2. COUNTERS (Compteurs cumulatifs simples) ---
orders_counter = meter.create_counter(
    "total_orders", 
    description="Total orders processed by the system"
)
aborts_counter = meter.create_counter(
    "total_2pc_aborts", 
    description="Total 2PC transaction aborts triggered"
)
prepare_votes_counter = meter.create_counter(
    "prepare_votes",
    description="Total 2PC prepare votes cast by participants"
)
transaction_outcomes_counter = meter.create_counter(
    "transaction_outcomes",
    description="Total 2PC outcomes by final decision"
)
fraud_checks_counter = meter.create_counter(
    "fraud_checks",
    description="Total fraud checks executed"
)

# --- 3. UP DOWN COUNTERS (Compteurs qui peuvent monter et descendre) ---
active_tx_counter = meter.create_up_down_counter(
    "active_prepared_transactions",
    description="Number of transactions currently in PREPARED state"
)
queue_size_counter = meter.create_up_down_counter(
    "current_queue_size",
    description="Current number of orders waiting in the queue"
)

# --- 4. HISTOGRAMS (Mesure de distribution de durées/latences) ---
tx_latency_histo = meter.create_histogram(
    "txn_2pc_latency", 
    unit="ms",
    description="Full 2PC process execution time"
)
read_latency_histo = meter.create_histogram(
    "quorum_read_latency", 
    unit="ms",
    description="Database Quorum Read response time"
)

# --- 5. ASYNCHRONOUS GAUGES (Mesure de valeurs d'état à la demande via Callbacks) ---
# Variables globales simulées pour les callbacks (à mettre à jour dans ton app.py)
_current_stock_value = 0
_alive_executors_count = 0
_current_leader_id = 0

def _book_stock_callback(options):
    global _current_stock_value
    yield metrics.Observation(_current_stock_value, {"book": "Dune"})

def _alive_executors_callback(options):
    global _alive_executors_count
    yield metrics.Observation(_alive_executors_count)

def _current_leader_id_callback(options):
    global _current_leader_id
    yield metrics.Observation(_current_leader_id)

meter.create_observable_gauge(
    "book_stock_level", 
    callbacks=[_book_stock_callback],
    description="Current stock level for tracking inventory"
)
meter.create_observable_gauge(
    "bully_alive_executors", 
    callbacks=[_alive_executors_callback],
    description="Number of healthy executor instances in the Bully cluster"
)
meter.create_observable_gauge(
    "current_leader_id",
    callbacks=[_current_leader_id_callback],
    description="Current Bully leader executor ID"
)