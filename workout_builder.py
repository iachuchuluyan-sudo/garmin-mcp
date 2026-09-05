"""
Creacion y agendado de workouts en Garmin Connect.

Los workouts solo configuran duracion/distancia de cada paso.
No prescriben zonas de FC ni targets — el atleta corre por
sensacion o ritmo propio.
"""
import datetime
import logging
import json

from garminconnect.workout import (
    RunningWorkout,
    CyclingWorkout,
    SwimmingWorkout,
    WorkoutSegment,
    ExecutableStep,
    TargetType,
    ConditionType,
    StepType,
    SportType,
    create_repeat_group,
)
from garmin_client import get_client

logger = logging.getLogger(__name__)


# ---------- Target vacio ----------

def _no_target() -> dict:
    return {
        "workoutTargetTypeId": TargetType.NO_TARGET,
        "workoutTargetTypeKey": "no.target",
        "displayOrder": 1,
    }


# ---------- End conditions ----------

def _time_end_condition() -> dict:
    return {
        "conditionTypeId": ConditionType.TIME,
        "conditionTypeKey": "time",
        "displayOrder": 2,
        "displayable": True,
    }


def _distance_end_condition() -> dict:
    return {
        "conditionTypeId": ConditionType.DISTANCE,
        "conditionTypeKey": "distance",
        "displayOrder": 2,
        "displayable": True,
    }


# ---------- Constructores de steps ----------

def warmup_step(step_order: int, duration_seconds: float) -> ExecutableStep:
    return ExecutableStep(
        stepOrder=step_order,
        stepType={"stepTypeId": StepType.WARMUP, "stepTypeKey": "warmup", "displayOrder": 1},
        endCondition=_time_end_condition(),
        endConditionValue=float(duration_seconds),
        targetType=_no_target(),
    )


def cooldown_step(step_order: int, duration_seconds: float) -> ExecutableStep:
    return ExecutableStep(
        stepOrder=step_order,
        stepType={"stepTypeId": StepType.COOLDOWN, "stepTypeKey": "cooldown", "displayOrder": 2},
        endCondition=_time_end_condition(),
        endConditionValue=float(duration_seconds),
        targetType=_no_target(),
    )


def run_time_step(step_order: int, duration_seconds: float) -> ExecutableStep:
    """Paso de carrera por tiempo, sin target."""
    return ExecutableStep(
        stepOrder=step_order,
        stepType={"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
        endCondition=_time_end_condition(),
        endConditionValue=float(duration_seconds),
        targetType=_no_target(),
    )


def run_distance_step(step_order: int, distance_meters: float) -> ExecutableStep:
    """Paso de carrera por distancia, sin target."""
    return ExecutableStep(
        stepOrder=step_order,
        stepType={"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
        endCondition=_distance_end_condition(),
        endConditionValue=float(distance_meters),
        targetType=_no_target(),
    )


def recovery_time_step(step_order: int, duration_seconds: float) -> ExecutableStep:
    """Recuperacion por tiempo entre intervalos, sin target."""
    return ExecutableStep(
        stepOrder=step_order,
        stepType={"stepTypeId": StepType.RECOVERY, "stepTypeKey": "recovery", "displayOrder": 4},
        endCondition=_time_end_condition(),
        endConditionValue=float(duration_seconds),
        targetType=_no_target(),
    )


def swim_step(step_order: int, distance_meters: float) -> ExecutableStep:
    """Paso de natacion por distancia, sin target."""
    return ExecutableStep(
        stepOrder=step_order,
        stepType={"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
        endCondition=_distance_end_condition(),
        endConditionValue=float(distance_meters),
        targetType=_no_target(),
    )


def swim_rest_step(step_order: int, duration_seconds: float) -> ExecutableStep:
    """Descanso de natacion por tiempo."""
    return ExecutableStep(
        stepOrder=step_order,
        stepType={"stepTypeId": StepType.RECOVERY, "stepTypeKey": "recovery", "displayOrder": 4},
        endCondition=_time_end_condition(),
        endConditionValue=float(duration_seconds),
        targetType=_no_target(),
    )


# ---------- Plantillas de workouts ----------

def build_easy_run(name: str, total_minutes: int,
                   distance_meters: int = None, description: str = "") -> RunningWorkout:
    """Rodaje: entrada en calor 10min + cuerpo principal + vuelta calma 10min.
    Si se pasa distance_meters el cuerpo usa distancia, si no usa tiempo."""
    warmup_secs = 600.0
    cooldown_secs = 600.0

    if distance_meters:
        main_step = run_distance_step(2, float(distance_meters))
    else:
        main_secs = max((total_minutes * 60) - warmup_secs - cooldown_secs, 600.0)
        main_step = run_time_step(2, main_secs)

    return RunningWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=int(total_minutes * 60),
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": SportType.RUNNING, "sportTypeKey": "running", "displayOrder": 1},
            workoutSteps=[
                warmup_step(1, warmup_secs),
                main_step,
                cooldown_step(3, cooldown_secs),
            ]
        )]
    )


def build_tempo_run(name: str, warmup_min: int, tempo_min: int,
                    cooldown_min: int, description: str = "") -> RunningWorkout:
    """Tempo: entrada + cuerpo principal + vuelta calma. Sin targets."""
    total = (warmup_min + tempo_min + cooldown_min) * 60
    return RunningWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=total,
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": SportType.RUNNING, "sportTypeKey": "running", "displayOrder": 1},
            workoutSteps=[
                warmup_step(1, warmup_min * 60),
                run_time_step(2, tempo_min * 60),
                cooldown_step(3, cooldown_min * 60),
            ]
        )]
    )


def build_interval_run(name: str, warmup_min: int, interval_distance_m: int,
                       repetitions: int, recovery_seconds: int, cooldown_min: int,
                       description: str = "") -> RunningWorkout:
    """Intervalos por distancia. Sin targets de FC."""
    interval_steps = []
    order = 1
    for i in range(repetitions):
        interval_steps.append(run_distance_step(order, float(interval_distance_m)))
        order += 1
        if i < repetitions - 1:
            interval_steps.append(recovery_time_step(order, float(recovery_seconds)))
            order += 1

    rep_group = create_repeat_group(repetitions, interval_steps, step_order=2)
    estimated = int((warmup_min + cooldown_min) * 60 + repetitions * (interval_distance_m / 3.5 + recovery_seconds))

    return RunningWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=estimated,
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": SportType.RUNNING, "sportTypeKey": "running", "displayOrder": 1},
            workoutSteps=[
                warmup_step(1, warmup_min * 60),
                rep_group,
                cooldown_step(3, cooldown_min * 60),
            ]
        )]
    )


def build_long_run(name: str, total_minutes: int,
                   distance_meters: int = None, description: str = "") -> RunningWorkout:
    """Tirada larga: entrada 10min + cuerpo principal + vuelta calma 10min.
    Sin division automatica de zonas — una sola pasada por la distancia/tiempo indicada."""
    warmup_secs = 600.0
    cooldown_secs = 600.0

    if distance_meters:
        main_step = run_distance_step(2, float(distance_meters))
    else:
        main_secs = max((total_minutes * 60) - warmup_secs - cooldown_secs, 600.0)
        main_step = run_time_step(2, main_secs)

    return RunningWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=int(total_minutes * 60),
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": SportType.RUNNING, "sportTypeKey": "running", "displayOrder": 1},
            workoutSteps=[
                warmup_step(1, warmup_secs),
                main_step,
                cooldown_step(3, cooldown_secs),
            ]
        )]
    )


def build_easy_bike(name: str, total_minutes: int, description: str = "") -> CyclingWorkout:
    """Rodaje de ciclismo: entrada 10min + cuerpo + vuelta calma 10min. Sin targets."""
    warmup_secs = 600.0
    cooldown_secs = 600.0
    main_secs = max((total_minutes * 60) - warmup_secs - cooldown_secs, 600.0)

    return CyclingWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=int(total_minutes * 60),
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": SportType.CYCLING, "sportTypeKey": "cycling", "displayOrder": 1},
            workoutSteps=[
                warmup_step(1, warmup_secs),
                run_time_step(2, main_secs),
                cooldown_step(3, cooldown_secs),
            ]
        )]
    )


def build_swim(name: str, pool_length_meters: int, total_distance_meters: int,
               interval_distance_meters: int = None, repetitions: int = None,
               rest_seconds: int = 20, description: str = "") -> SwimmingWorkout:
    """Sesion de natacion en pileta. Sin targets.
    Con series: warmup 200m + N x interval_distance + cooldown 200m.
    Sin series: una sola pasada por total_distance_meters."""
    sport_type = {
        "sportTypeId": SportType.SWIMMING,
        "sportTypeKey": "lap_swimming",
        "displayOrder": 3,
    }

    if interval_distance_meters and repetitions:
        warmup_dist = min(200, int(total_distance_meters * 0.2))
        cooldown_dist = warmup_dist

        series_steps = []
        order = 1
        for i in range(repetitions):
            series_steps.append(swim_step(order, float(interval_distance_meters)))
            order += 1
            if i < repetitions - 1:
                series_steps.append(swim_rest_step(order, float(rest_seconds)))
                order += 1

        rep_group = create_repeat_group(repetitions, series_steps, step_order=2)
        estimated = int(total_distance_meters / 1.5 + repetitions * rest_seconds)

        steps = [
            swim_step(1, float(warmup_dist)),
            rep_group,
            swim_step(3, float(cooldown_dist)),
        ]
    else:
        estimated = int(total_distance_meters / 1.5)
        steps = [swim_step(1, float(total_distance_meters))]

    return SwimmingWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=estimated,
        poolLength=float(pool_length_meters),
        poolLengthUnit={"unitId": 2, "unitKey": "meter", "factor": 1.0},
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType=sport_type,
            workoutSteps=steps,
        )]
    )


# ---------- Upload / schedule / consulta ----------

def upload_and_schedule_workout(workout_obj, date_str: str) -> dict:
    """Sube un workout a Garmin Connect y lo agenda en la fecha indicada."""
    client = get_client()
    workout_dict = workout_obj.to_dict()
    logger.info("Subiendo workout: %s", json.dumps(workout_dict, indent=2))

    try:
        uploaded = client.upload_workout(workout_dict)
        logger.info("Respuesta upload: %s", uploaded)
    except Exception as e:
        logger.error("Error en upload_workout: %s", str(e))
        return {"error": f"Error al subir workout a Garmin: {str(e)}"}

    workout_id = uploaded.get("workoutId") if isinstance(uploaded, dict) else None
    if not workout_id:
        return {"error": "No se pudo obtener el workoutId", "raw": str(uploaded)}

    try:
        scheduled = client.schedule_workout(workout_id, date_str)
        logger.info("Respuesta schedule: %s", scheduled)
    except Exception as e:
        logger.error("Error en schedule_workout: %s", str(e))
        return {"error": f"Workout subido (id: {workout_id}) pero falló al agendarlo: {str(e)}"}

    scheduled_id = None
    if isinstance(scheduled, dict):
        scheduled_id = scheduled.get("id") or scheduled.get("scheduledWorkoutId")

    return {
        "workout_id": workout_id,
        "scheduled_id": scheduled_id,
        "name": workout_obj.workoutName,
        "date": date_str,
        "status": "agendado",
    }


def get_scheduled_workouts_for_week(date_str: str = None) -> list:
    """Workouts agendados en Garmin para el mes de la fecha indicada."""
    client = get_client()
    d = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    try:
        data = client.get_scheduled_workouts(d.year, d.month)
        return data if isinstance(data, list) else [data]
    except Exception as e:
        return [{"error": str(e)}]


def delete_scheduled_workout(scheduled_workout_id: str) -> dict:
    """Borra un workout del calendario de Garmin por su scheduled_id."""
    client = get_client()
    try:
        client.unschedule_workout(scheduled_workout_id)
        return {"status": "borrado", "scheduled_id": scheduled_workout_id}
    except Exception as e:
        return {"error": str(e)}
