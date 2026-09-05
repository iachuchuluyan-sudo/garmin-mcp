"""
Cliente de Garmin Connect.
Usa la libreria no oficial `garminconnect`, que se autentica con
usuario/contraseña reales de Garmin Connect.

Cache en memoria con TTL diferenciado por tipo de dato:
- 10 min: datos del dia (sueño, HRV, body battery, readiness, SpO2, respiracion)
- 60 min: datos estables (VO2max, FTP, race predictions, records, endurance)
-  5 min: actividades recientes
"""
import os
import time
import logging
import datetime
from garminconnect import Garmin

logger = logging.getLogger(__name__)

_client = None
_cache: dict = {}

TTL_DAY_METRICS = 600
TTL_STABLE      = 3600
TTL_ACTIVITIES  = 300


def get_client():
    global _client
    if _client is not None:
        return _client
    client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    client.login()
    _client = client
    logger.info("Sesion de Garmin iniciada")
    return client


def _date_str(d: datetime.date) -> str:
    return d.strftime("%Y-%m-%d")


def _cached(key: str, ttl: int, fn):
    now = time.time()
    entry = _cache.get(key)
    if entry is not None:
        ts, data = entry
        if now - ts < ttl:
            logger.debug("Cache hit: %s", key)
            return data
    logger.debug("Cache miss: %s", key)
    result = fn()
    _cache[key] = (now, result)
    return result


def get_sleep(days: int = 7):
    def fetch():
        client = get_client()
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days - 1)
        sleep_scores_by_date = {}
        try:
            daily_stats = client.get_sleep_daily(_date_str(start), _date_str(today))
            for stat in daily_stats or []:
                cal_date = stat.get("calendarDate")
                if cal_date:
                    sleep_scores_by_date[cal_date] = (
                        stat.get("sleepScore")
                        or stat.get("overallSleepScore")
                        or stat.get("sleepQuality")
                    )
        except Exception:
            pass
        results = []
        for i in range(days):
            day = today - datetime.timedelta(days=i)
            day_str = _date_str(day)
            try:
                data = client.get_sleep_data(day_str)
                if data and data.get("dailySleepDTO"):
                    dto = data["dailySleepDTO"]
                    sleep_scores = data.get("sleepScores") or {}
                    sleep_score = (
                        sleep_scores_by_date.get(day_str)
                        or sleep_scores.get("overall", {}).get("value")
                        or sleep_scores.get("overallScore")
                        or data.get("sleepScore")
                        or dto.get("sleepScore")
                    )
                    results.append({
                        "date": day_str,
                        "sleep_time_seconds": dto.get("sleepTimeSeconds"),
                        "deep_sleep_seconds": dto.get("deepSleepSeconds"),
                        "light_sleep_seconds": dto.get("lightSleepSeconds"),
                        "rem_sleep_seconds": dto.get("remSleepSeconds"),
                        "awake_seconds": dto.get("awakeSleepSeconds"),
                        "sleep_score": sleep_score,
                        "avg_overnight_hrv": data.get("avgOvernightHrv"),
                        "resting_heart_rate": data.get("restingHeartRate"),
                    })
            except Exception:
                continue
        return results
    return _cached(f"sleep_{days}", TTL_DAY_METRICS, fetch)


def get_hrv(days: int = 7):
    def fetch():
        client = get_client()
        today = datetime.date.today()
        results = []
        for i in range(days):
            day = today - datetime.timedelta(days=i)
            try:
                data = client.get_hrv_data(_date_str(day))
                if data and data.get("hrvSummary"):
                    summary = data["hrvSummary"]
                    results.append({
                        "date": _date_str(day),
                        "last_night_avg": summary.get("lastNightAvg"),
                        "last_night_5min_high": summary.get("lastNight5MinHigh"),
                        "status": summary.get("status"),
                        "weekly_avg": summary.get("weeklyAvg"),
                        "baseline_low": (summary.get("baseline") or {}).get("balancedLow"),
                        "baseline_high": (summary.get("baseline") or {}).get("balancedUpper"),
                    })
            except Exception:
                continue
        return results
    return _cached(f"hrv_{days}", TTL_DAY_METRICS, fetch)


def get_body_battery(days: int = 7):
    def fetch():
        client = get_client()
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days - 1)
        try:
            data = client.get_body_battery(_date_str(start), _date_str(today))
            return [{"date": e.get("date"), "charged": e.get("charged"), "drained": e.get("drained"),
                     "highest": e.get("highestLevel"), "lowest": e.get("lowestLevel")} for e in data or []]
        except Exception as e:
            return {"error": str(e)}
    return _cached(f"body_battery_{days}", TTL_DAY_METRICS, fetch)


def get_training_readiness():
    def fetch():
        client = get_client()
        today = _date_str(datetime.date.today())
        try:
            data = client.get_training_readiness(today)
            if not data:
                return {}
            entry = data[0] if isinstance(data, list) else data
            return {
                "score": entry.get("score"),
                "level": entry.get("level"),
                "feedback": entry.get("feedbackLong") or entry.get("feedbackShort"),
                "sleep_score_factor": entry.get("sleepScoreFactorPercent"),
                "hrv_factor": entry.get("hrvFactorPercent"),
                "recovery_time_factor": entry.get("recoveryTimeFactorPercent"),
                "training_load_factor": entry.get("acuteLoadFactorPercent"),
            }
        except Exception as e:
            return {"error": str(e)}
    return _cached("training_readiness", TTL_DAY_METRICS, fetch)


def get_training_status():
    def fetch():
        client = get_client()
        today = _date_str(datetime.date.today())
        try:
            return client.get_training_status(today)
        except Exception as e:
            return {"error": str(e)}
    return _cached("training_status", TTL_DAY_METRICS, fetch)


def get_max_metrics():
    def fetch():
        client = get_client()
        today = _date_str(datetime.date.today())
        try:
            data = client.get_max_metrics(today)
            if not data:
                return {}
            entry = data[0] if isinstance(data, list) else data
            return {
                "calendar_date": entry.get("calendarDate"),
                "vo2max_running": entry.get("vo2MaxPreciseValue") or entry.get("vo2MaxValue"),
                "vo2max_cycling": entry.get("vo2MaxCyclingValue"),
                "fitness_age": entry.get("fitnessAge"),
                "fitness_age_description": entry.get("fitnessAgeDescription"),
            }
        except Exception as e:
            return {"error": str(e)}
    return _cached("max_metrics", TTL_STABLE, fetch)


def get_race_predictions():
    def fetch():
        client = get_client()
        try:
            data = client.get_race_predictions()
            if not data:
                return {}
            return {
                "time_5k_seconds": data.get("time5K") or data.get("racePrediction5k"),
                "time_10k_seconds": data.get("time10K") or data.get("racePrediction10k"),
                "time_half_marathon_seconds": data.get("timeHalfMarathon") or data.get("racePredictionHalfMarathon"),
                "time_marathon_seconds": data.get("timeMarathon") or data.get("racePredictionMarathon"),
                "raw": data,
            }
        except Exception as e:
            return {"error": str(e)}
    return _cached("race_predictions", TTL_STABLE, fetch)


def get_lactate_threshold():
    def fetch():
        client = get_client()
        try:
            data = client.get_lactate_threshold(latest=True)
            if not data:
                return {}
            entry = data[0] if isinstance(data, list) else data
            return {
                "date": entry.get("calendarDate"),
                "heart_rate_bpm": entry.get("heartRate") or (entry.get("lactateThresholdHeartRate") or {}).get("value"),
                "pace_seconds_per_km": entry.get("pace") or (entry.get("lactateThresholdPace") or {}).get("value"),
                "raw": entry,
            }
        except Exception as e:
            return {"error": str(e)}
    return _cached("lactate_threshold", TTL_STABLE, fetch)


def get_endurance_score(days: int = 28):
    def fetch():
        client = get_client()
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days - 1)
        try:
            return client.get_endurance_score(_date_str(start), _date_str(today))
        except Exception as e:
            return {"error": str(e)}
    return _cached(f"endurance_score_{days}", TTL_STABLE, fetch)


def get_running_tolerance(days: int = 28):
    def fetch():
        client = get_client()
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days - 1)
        try:
            return client.get_running_tolerance(_date_str(start), _date_str(today), aggregation="weekly")
        except Exception as e:
            return {"error": str(e)}
    return _cached(f"running_tolerance_{days}", TTL_STABLE, fetch)


def get_personal_records():
    def fetch():
        client = get_client()
        try:
            return client.get_personal_record()
        except Exception as e:
            return {"error": str(e)}
    return _cached("personal_records", TTL_STABLE, fetch)


def get_respiration_data():
    def fetch():
        client = get_client()
        today = _date_str(datetime.date.today())
        try:
            return client.get_respiration_data(today)
        except Exception as e:
            return {"error": str(e)}
    return _cached("respiration", TTL_DAY_METRICS, fetch)


def get_spo2_data():
    def fetch():
        client = get_client()
        today = _date_str(datetime.date.today())
        try:
            return client.get_spo2_data(today)
        except Exception as e:
            return {"error": str(e)}
    return _cached("spo2", TTL_DAY_METRICS, fetch)


def get_hill_score(days: int = 28):
    def fetch():
        client = get_client()
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days - 1)
        try:
            return client.get_hill_score(_date_str(start), _date_str(today))
        except Exception as e:
            return {"error": str(e)}
    return _cached(f"hill_score_{days}", TTL_STABLE, fetch)


def get_activities(limit: int = 10):
    def fetch():
        client = get_client()
        try:
            activities = client.get_activities(0, limit)
            return [{
                "activity_id": a.get("activityId"),
                "name": a.get("activityName"),
                "type": (a.get("activityType") or {}).get("typeKey"),
                "start_time": a.get("startTimeLocal"),
                "duration_seconds": a.get("duration"),
                "distance_meters": a.get("distance"),
                "avg_hr": a.get("averageHR"),
                "max_hr": a.get("maxHR"),
                "calories": a.get("calories"),
                "training_effect_aerobic": a.get("aerobicTrainingEffect"),
                "training_effect_anaerobic": a.get("anaerobicTrainingEffect"),
            } for a in activities]
        except Exception as e:
            return {"error": str(e)}
    return _cached(f"activities_{limit}", TTL_ACTIVITIES, fetch)


def get_activity_detail(activity_id: str):
    """Detalle completo de una actividad de Garmin por su ID."""
    def fetch():
        client = get_client()
        try:
            return client.get_activity_details(activity_id)
        except Exception as e:
            return {"error": str(e)}
    return _cached(f"activity_detail_{activity_id}", TTL_ACTIVITIES, fetch)


def get_menstrual_calendar(days: int = 90):
    """Resumen de ciclos menstruales del ultimo periodo.
    Incluye duracion del ciclo, fases, predicciones y sintomas registrados."""
    def fetch():
        client = get_client()
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days - 1)
        try:
            return client.get_menstrual_calendar_data(_date_str(start), _date_str(today))
        except Exception as e:
            return {"error": str(e)}
    return _cached(f"menstrual_calendar_{days}", TTL_STABLE, fetch)


def get_menstrual_data_today():
    """Datos del ciclo menstrual para hoy: fase actual (menstruacion, folicular,
    ovulacion, lutea), sintomas registrados y nivel de energia esperado segun la fase.
    Fundamental para interpretar correctamente el HRV y la recuperacion."""
    def fetch():
        client = get_client()
        today = _date_str(datetime.date.today())
        try:
            return client.get_menstrual_data_for_date(today)
        except Exception as e:
            return {"error": str(e)}
    return _cached("menstrual_today", TTL_DAY_METRICS, fetch)
