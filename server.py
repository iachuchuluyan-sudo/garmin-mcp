"""
Servidor MCP: conecta Garmin Connect como herramientas que Claude puede usar.
Version solo-Garmin (sin Strava).
"""
import os
import logging
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings

import garmin_client
import workout_builder
from auth_provider import provider as oauth_provider, login_routes

SERVER_URL  = os.environ["SERVER_URL"].rstrip("/")
SERVER_HOST = urlparse(SERVER_URL).netloc

mcp = FastMCP(
    "garmin-coach",
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=SERVER_URL,
        resource_server_url=f"{SERVER_URL}/mcp",
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp"],
            default_scopes=["mcp"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[SERVER_HOST, "localhost", "localhost:8000", "127.0.0.1:8000"],
        allowed_origins=[SERVER_URL, "http://localhost:8000", "http://127.0.0.1:8000"],
    ),
)


# ---------- Recuperación y estado diario ----------

@mcp.tool()
def garmin_get_sleep(days: int = 7) -> list:
    """Datos de sueño de los ultimos N dias: horas, fases, sleep score, HRV nocturno, FC reposo."""
    return garmin_client.get_sleep(days)


@mcp.tool()
def garmin_get_hrv(days: int = 7) -> list:
    """HRV diario de los ultimos N dias, estado y rango base personal."""
    return garmin_client.get_hrv(days)


@mcp.tool()
def garmin_get_body_battery(days: int = 7) -> list:
    """Niveles de Body Battery de los ultimos N dias."""
    return garmin_client.get_body_battery(days)


@mcp.tool()
def garmin_get_respiration_data() -> dict:
    """Frecuencia respiratoria durante el sueño de anoche."""
    return garmin_client.get_respiration_data()


@mcp.tool()
def garmin_get_spo2_data() -> dict:
    """Saturacion de oxigeno nocturna de anoche."""
    return garmin_client.get_spo2_data()


@mcp.tool()
def garmin_get_training_readiness() -> dict:
    """Puntaje de preparacion para entrenar hoy."""
    return garmin_client.get_training_readiness()


# ---------- Carga y progresión ----------

@mcp.tool()
def garmin_get_training_status() -> dict:
    """Estado de carga de entrenamiento (productive / maintaining / overreaching / detraining)."""
    return garmin_client.get_training_status()


@mcp.tool()
def garmin_get_endurance_score(days: int = 28) -> dict:
    """Resistencia aerobica acumulada de los ultimos N dias."""
    return garmin_client.get_endurance_score(days)


@mcp.tool()
def garmin_get_running_tolerance(days: int = 28) -> list:
    """Tolerancia al running de las ultimas semanas: absorcion de carga y riesgo de lesion."""
    return garmin_client.get_running_tolerance(days)


@mcp.tool()
def garmin_get_hill_score(days: int = 28) -> dict:
    """Capacidad en subidas y potencia de los ultimos N dias."""
    return garmin_client.get_hill_score(days)


# ---------- Performance y proyección ----------

@mcp.tool()
def garmin_get_max_metrics() -> dict:
    """VO2max de running y ciclismo, fitness age."""
    return garmin_client.get_max_metrics()


@mcp.tool()
def garmin_get_race_predictions() -> dict:
    """Predicciones de tiempo para 5K, 10K, media maraton y maraton."""
    return garmin_client.get_race_predictions()


@mcp.tool()
def garmin_get_lactate_threshold() -> dict:
    """Umbral de lactato: ritmo y FC en el umbral."""
    return garmin_client.get_lactate_threshold()


@mcp.tool()
def garmin_get_personal_records() -> dict:
    """Records personales registrados en Garmin."""
    return garmin_client.get_personal_records()


# ---------- Actividades ----------

@mcp.tool()
def garmin_get_activities(limit: int = 10) -> list:
    """Ultimas N actividades de Garmin con FC, training effect, duracion y distancia."""
    return garmin_client.get_activities(limit)


# ---------- Salud femenina y ciclo menstrual ----------

@mcp.tool()
def garmin_get_menstrual_calendar(days: int = 90) -> dict:
    """Resumen de ciclos menstruales de los ultimos N dias.
    Incluye duracion del ciclo, fases, predicciones y sintomas registrados.
    Usar para entender patrones del ciclo y cruzarlos con la carga de entrenamiento."""
    return garmin_client.get_menstrual_calendar(days)


@mcp.tool()
def garmin_get_menstrual_data_today() -> dict:
    """Datos del ciclo menstrual para hoy: fase actual (menstruacion, folicular,
    ovulacion, lutea), sintomas registrados y nivel de energia esperado.
    IMPORTANTE: siempre llamar esta herramienta junto con HRV y body battery
    para interpretar correctamente la recuperacion — el HRV y la temperatura
    corporal varian naturalmente segun la fase del ciclo, y sin este contexto
    un HRV bajo puede interpretarse erroneamente como mala recuperacion."""
    return garmin_client.get_menstrual_data_today()


# ---------- Análisis de actividades ----------

@mcp.tool()
def garmin_get_activity_detail(activity_id: str) -> dict:
    """Detalle completo de una actividad de Garmin por su ID.
    Incluye splits, zonas de FC, training effect y metricas avanzadas."""
    return garmin_client.get_activity_detail(activity_id)


# ---------- Workouts (crear y agendar en Garmin) ----------

@mcp.tool()
def garmin_schedule_easy_run(
    name: str,
    date: str,
    total_minutes: int,
    distance_meters: int = None,
    description: str = ""
) -> dict:
    """Crea un rodaje suave y lo agenda en Garmin.
    Parametros: name, date (YYYY-MM-DD), total_minutes,
    distance_meters (opcional), description (opcional)."""
    w = workout_builder.build_easy_run(name, total_minutes, distance_meters, description)
    return workout_builder.upload_and_schedule_workout(w, date)


@mcp.tool()
def garmin_schedule_tempo_run(
    name: str,
    date: str,
    warmup_minutes: int,
    tempo_minutes: int,
    cooldown_minutes: int,
    description: str = ""
) -> dict:
    """Crea un tempo run y lo agenda en Garmin.
    Parametros: name, date (YYYY-MM-DD), warmup_minutes,
    tempo_minutes, cooldown_minutes, description (opcional)."""
    w = workout_builder.build_tempo_run(name, warmup_minutes, tempo_minutes, cooldown_minutes, description)
    return workout_builder.upload_and_schedule_workout(w, date)


@mcp.tool()
def garmin_schedule_interval_run(
    name: str,
    date: str,
    warmup_minutes: int,
    interval_distance_meters: int,
    repetitions: int,
    recovery_seconds: int,
    cooldown_minutes: int,
    description: str = ""
) -> dict:
    """Crea una sesion de intervalos por distancia y la agenda en Garmin.
    Parametros: name, date (YYYY-MM-DD), warmup_minutes,
    interval_distance_meters, repetitions, recovery_seconds,
    cooldown_minutes, description (opcional)."""
    w = workout_builder.build_interval_run(
        name, warmup_minutes, interval_distance_meters,
        repetitions, recovery_seconds, cooldown_minutes, description
    )
    return workout_builder.upload_and_schedule_workout(w, date)


@mcp.tool()
def garmin_schedule_long_run(
    name: str,
    date: str,
    total_minutes: int,
    distance_meters: int = None,
    description: str = ""
) -> dict:
    """Crea una tirada larga y la agenda en Garmin.
    Parametros: name, date (YYYY-MM-DD), total_minutes,
    distance_meters (opcional), description (opcional)."""
    w = workout_builder.build_long_run(name, total_minutes, distance_meters, description)
    return workout_builder.upload_and_schedule_workout(w, date)


@mcp.tool()
def garmin_schedule_easy_bike(
    name: str,
    date: str,
    total_minutes: int,
    description: str = ""
) -> dict:
    """Crea un rodaje suave de ciclismo y lo agenda en Garmin.
    Parametros: name, date (YYYY-MM-DD), total_minutes, description (opcional)."""
    w = workout_builder.build_easy_bike(name, total_minutes, description)
    return workout_builder.upload_and_schedule_workout(w, date)


@mcp.tool()
def garmin_schedule_swim(
    name: str,
    date: str,
    pool_length_meters: int,
    total_distance_meters: int,
    interval_distance_meters: int = None,
    repetitions: int = None,
    rest_seconds: int = 20,
    description: str = ""
) -> dict:
    """Crea una sesion de natacion y la agenda en Garmin.
    Parametros: name, date (YYYY-MM-DD), pool_length_meters (25 o 50),
    total_distance_meters, interval_distance_meters (opcional),
    repetitions (opcional), rest_seconds (default 20), description (opcional)."""
    w = workout_builder.build_swim(
        name, pool_length_meters, total_distance_meters,
        interval_distance_meters, repetitions, rest_seconds, description
    )
    return workout_builder.upload_and_schedule_workout(w, date)


@mcp.tool()
def garmin_get_scheduled_workouts(date: str = "") -> list:
    """Workouts ya agendados en Garmin para el mes de la fecha indicada (YYYY-MM-DD).
    Usar antes de agendar para no duplicar sesiones."""
    return workout_builder.get_scheduled_workouts_for_week(date or None)


@mcp.tool()
def garmin_delete_scheduled_workout(scheduled_workout_id: str) -> dict:
    """Borra un workout del calendario de Garmin por su scheduled_id."""
    return workout_builder.delete_scheduled_workout(scheduled_workout_id)


# App ASGI
app = mcp.streamable_http_app()
for route in login_routes:
    app.router.routes.append(route)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
