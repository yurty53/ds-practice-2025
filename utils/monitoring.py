import os

# Try to import OpenTelemetry; if unavailable, provide no-op fallbacks so services
# can start even when dependencies are not installed (useful in constrained CI
# or when observability stack isn't available).
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import SpanExportResult
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, MetricExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics.export import MetricExportResult

    class _ResilientSpanExporter(SpanExporter):
        def __init__(self, exporter):
            self._exporter = exporter

        def __getattr__(self, name):
            return getattr(self._exporter, name)

        def export(self, spans, *args, **kwargs):
            try:
                return self._exporter.export(spans, *args, **kwargs)
            except Exception:
                return SpanExportResult.FAILURE

        def shutdown(self, *args, **kwargs):
            try:
                return self._exporter.shutdown(*args, **kwargs)
            except Exception:
                return None

        def force_flush(self, timeout_millis=30000, *args, **kwargs):
            try:
                return self._exporter.force_flush(timeout_millis, *args, **kwargs)
            except Exception:
                return False

    class _ResilientMetricExporter(MetricExporter):
        def __init__(self, exporter):
            self._exporter = exporter
            self._preferred_temporality = getattr(exporter, "_preferred_temporality", None)
            self._preferred_aggregation = getattr(exporter, "_preferred_aggregation", None)

        def __getattr__(self, name):
            return getattr(self._exporter, name)

        def export(self, metrics_data, timeout_millis=10000, *args, **kwargs):
            try:
                return self._exporter.export(metrics_data, timeout_millis, *args, **kwargs)
            except Exception:
                return MetricExportResult.FAILURE

        def shutdown(self, *args, **kwargs):
            try:
                return self._exporter.shutdown(*args, **kwargs)
            except Exception:
                return None

        def force_flush(self, timeout_millis=10000, *args, **kwargs):
            try:
                return self._exporter.force_flush(timeout_millis, *args, **kwargs)
            except Exception:
                return False

    # Dynamic service identifier comes from environment in each container
    SERVICE_IDENTIFIER = os.getenv("SERVICE_NAME", "bookstore-service")
    INSTANCE_ID = os.getenv("SERVICE_INSTANCE_ID")

    _resource_attrs = {SERVICE_NAME: SERVICE_IDENTIFIER}
    if INSTANCE_ID:
        _resource_attrs["service.instance.id"] = INSTANCE_ID

    resource = Resource.create(attributes=_resource_attrs)

    # Traces
    tracerProvider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(
        _ResilientSpanExporter(
            OTLPSpanExporter(endpoint="http://observability:4318/v1/traces")
        )
    )
    tracerProvider.add_span_processor(processor)
    trace.set_tracer_provider(tracerProvider)
    tracer = trace.get_tracer(f"{SERVICE_IDENTIFIER}-tracer")

    # Metrics (OTLP HTTP)
    reader = PeriodicExportingMetricReader(
        _ResilientMetricExporter(
            OTLPMetricExporter(endpoint="http://observability:4318/v1/metrics")
        ),
        export_interval_millis=1000
    )
    meterProvider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meterProvider)
    meter = metrics.get_meter(f"{SERVICE_IDENTIFIER}-meter")

    _OTEL_AVAILABLE = True
except Exception:
    # opentelemetry not available or failed to initialize — provide safe no-op
    # implementations that match the methods used by the codebase so imports
    # don't crash the process. This prevents ModuleNotFoundError and avoids
    # noisy tracebacks when the observability stack isn't present.
    _OTEL_AVAILABLE = False

    class _Noop:
        def __getattr__(self, name):
            def _noop(*args, **kwargs):
                return None
            return _noop

    class _NoopTracer:
        class _Span:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def set_attribute(self, *args, **kwargs):
                return None
            def add_event(self, *args, **kwargs):
                return None
            def record_exception(self, *args, **kwargs):
                return None
            def set_status(self, *args, **kwargs):
                return None
            def update_name(self, *args, **kwargs):
                return None
            def end(self, *args, **kwargs):
                return None
            def is_recording(self):
                return False

        def start_as_current_span(self, name, **kwargs):
            return _NoopTracer._Span()

    class _NoopMetric:
        def create_counter(self, *a, **k):
            return _Noop()
        def create_up_down_counter(self, *a, **k):
            return _Noop()
        def create_histogram(self, *a, **k):
            return _Noop()
        def create_observable_gauge(self, *a, **k):
            return None

    tracer = _NoopTracer()
    meter = _NoopMetric()
    class _Observation:
        def __init__(self, value, attributes=None):
            self.value = value
            self.attributes = attributes or {}

    class _MetricsModule:
        Observation = _Observation

    metrics = _MetricsModule()

# Counters
orders_counter = meter.create_counter(
    "total_orders", 
    description="Total orders processed by the system"
)
aborts_counter = meter.create_counter(
    "total_2pc_aborts", 
    description="Total 2PC transaction aborts triggered"
)
prepare_votes_counter = meter.create_counter(
    "prepare_votes_total",
    description="Total 2PC prepare votes cast by participants"
)
transaction_outcomes_counter = meter.create_counter(
    "transaction_outcomes_total",
    description="Total 2PC outcomes by final decision"
)
fraud_checks_counter = meter.create_counter(
    "fraud_checks_total",
    description="Total fraud checks executed"
)
checkout_requests_counter = meter.create_counter(
    "checkout_requests_total",
    description="Total /checkout requests by final outcome"
)
fraud_rejections_counter = meter.create_counter(
    "fraud_rejections_total",
    description="Fraud checks that rejected a transaction"
)
verification_checks_counter = meter.create_counter(
    "verification_checks_total",
    description="Transaction verification check outcomes"
)
suggestions_served_counter = meter.create_counter(
    "suggestions_served_total",
    description="Suggestion responses returned"
)
enqueue_total = meter.create_counter(
    "order_queue_enqueue_total",
    description="Successful enqueue operations on order_queue"
)
dequeue_total = meter.create_counter(
    "order_queue_dequeue_total",
    description="Successful dequeue operations on order_queue"
)
bully_elections_started_counter = meter.create_counter(
    "bully_elections_started_total",
    description="Bully leader elections initiated"
)

# Up/down counters
active_tx_counter = meter.create_up_down_counter(
    "active_prepared_transactions",
    description="Number of transactions currently in PREPARED state"
)
queue_size_counter = meter.create_up_down_counter(
    "current_queue_size",
    description="Current number of orders waiting in the queue"
)

# Histograms
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
verification_latency_histo = meter.create_histogram(
    "verification_latency",
    unit="ms",
    description="Transaction verification RPC latency"
)
suggestion_latency_histo = meter.create_histogram(
    "suggestion_latency",
    unit="ms",
    description="Suggestions RPC latency"
)

# Observable gauges (shared state variables)
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

# Helper accessors for services to update shared state
def set_stock(value: int):
    global _current_stock_value
    try:
        _current_stock_value = int(value)
    except (TypeError, ValueError):
        pass

def get_stock() -> int:
    return int(_current_stock_value)

def reset_stock():
    global _current_stock_value
    _current_stock_value = int(_current_stock_value) + 100
    return _current_stock_value

def decrement_stock():
    global _current_stock_value
    try:
        if int(_current_stock_value) > 0:
            _current_stock_value = int(_current_stock_value) - 1
    except Exception:
        pass
    return _current_stock_value

def get_current_stock() -> int:
    return int(_current_stock_value)
