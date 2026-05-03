# =============================================================

#L’algorithme décisionnel du bracelet repose sur une approche hybride combinant règles physiologiques, 
#analyse temporelle et estimation probabiliste. Les données envoyées par le bracelet via le JavaScript 
#incluent le profil utilisateur, les mesures en temps réel (température, humidité, conductivité, 
#interactions) ainsi que des variables dérivées comme le bilan hydrique et la perte estimée de fluides. 
#Le système Python enrichit ces données en construisant un historique temporel, en calculant des 
#statistiques (moyennes, pentes, variabilité) et en mettant à jour une baseline individuelle. Trois 
#sous-scores principaux sont ensuite évalués : le stress thermique (lié à la chaleur, humidité et activité), 
#le déficit hydrique (basé sur le bilan eau-perte et les facteurs médicaux) et le pattern de sudation 
#(détection et évolution de la transpiration). Ces scores sont ajustés par des facteurs dynamiques comme 
#les tendances temporelles et les écarts à la baseline. En parallèle, une probabilité de risque est 
#estimée via une fonction logistique inspirée du machine learning. L’ensemble est fusionné pour produire 
#un score global de risque, converti en niveaux (LOW à CRITICAL), accompagné d’un niveau de confiance, 
#de facteurs explicatifs et d’une recommandation, puis renvoyé à l’interface web pour affichage en temps 
#réel.
# =============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from collections import deque
from pathlib import Path
import math
import statistics
import time
import csv
import os
from datetime import datetime, timezone
import json
import joblib
import pandas as pd

# API FastAPI qui reçoit les données préparées par le JavaScript et renvoie la décision clinique.

app = FastAPI(title="Bracelet Decision API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Historique en mémoire par participant pour calculer les moyennes, pentes et tendances temporelles.
# BASELINE_STORE mémorise une ligne de base progressive propre à chaque participant.

SESSION_HISTORY: Dict[str, deque] = {}
BASELINE_STORE: Dict[str, Dict[str, Any]] = {}

MAX_HISTORY = 1800

DATA_DIR = Path("data_JOURNEETEST")
DATA_DIR.mkdir(exist_ok=True)

MASTER_CSV = DATA_DIR / "participants_master.csv"
EVENTS_CSV = DATA_DIR / "participant_events.csv"

ML_DIR = Path("ml_saved_model")
ML_MODEL = None
ML_SCALER = None
ML_FEATURES = None
ML_MODEL_ERROR = None

try:
    ML_MODEL = joblib.load(ML_DIR / "bracelet_logreg_model.joblib")
    ML_SCALER = joblib.load(ML_DIR / "bracelet_scaler.joblib")
    with open(ML_DIR / "bracelet_features.json", "r", encoding="utf-8") as f:
        ML_FEATURES = json.load(f)
except Exception as e:
    ML_MODEL_ERROR = str(e)


# Modèle Pydantic des informations fixes ou semi-fixes saisies dans la page web.
# Ce bloc correspond à profile dans le payload JS envoyé vers /predict.

class Profile(BaseModel):
    fullName: str = ""
    age: Optional[float] = 0
    sex: str = "N/A"
    weightKg: Optional[float] = 0
    heightCm: Optional[float] = 0
    activity: str = "Sédentaire"
    fitnessLevel: str = "Moyen"
    acclimatization: str = "Partielle"
    historyHeatIllness: str = "Non"
    medicalRiskFlag: str = "Non"
    medicationRiskFlag: str = "Non"
    indoorOutdoor: str = "Intérieur"
    sunExposure: str = "Non"
    airMovementLevel: str = "Moyen"
    ppeUsed: str = "Non"
    clothingLevel: str = "Normal"
    activityIntensity: Optional[float] = 1
    waterMlPerPress: Optional[float] = 250
    restBreakRecent: str = "Oui"
    ambient: str = ""
    conditions: str = ""
    notes: str = ""

    kidneyDisease: str = "Non"
    diabetes: str = "Non"
    heartFailure: str = "Non"
    cognitiveRisk: str = "Non"
    swallowingDifficulty: str = "Non"
    diureticUse: str = "Non"
    laxativeUse: str = "Non"
    feverToday: str = "Non"
    vomitingDiarrheaToday: str = "Non"
    accessToWater: str = "Facile"
    caregiverPrompting: str = "Non"
    usualDailyWaterIntakeMl: Optional[float] = 0
    targetHourlyIntakeMl: Optional[float] = 0
    baselineHydrationRisk: str = "Modéré"
    plannedExposureDurationMin: Optional[float] = 0


# Modèle Pydantic des mesures instantanées provenant de l’ESP32 et affichées dans le front-end.

class Measurements(BaseModel):
    tempC: Optional[float] = None
    humPct: Optional[float] = None
    adcRaw: Optional[float] = None
    touch: Optional[Any] = None
    touchCount: Optional[float] = 0
    led: Optional[bool] = None
    led_jaune: Optional[bool] = None
    buzzer: Optional[bool] = None
    acEnabled: Optional[bool] = None
    acSignalFreqHz: Optional[float] = None
    pwmCarrierFreqHz: Optional[float] = None
    acPin: Optional[float] = None
    acPeriodUs: Optional[float] = None
    acPhaseUs: Optional[float] = None
    acInstantV: Optional[float] = None
    acVHigh: Optional[float] = None
    acVLow: Optional[float] = None


# Modèle Pydantic des variables déjà dérivées côté JS ou calculées ensuite côté Python.
# On y retrouve notamment le bilan hydrique, la détection de sueur et les indicateurs temporels.

class Derived(BaseModel):
    bmi: Optional[float] = None
    heatIndexC: Optional[float] = None
    sweatDetected: Optional[bool] = None
    conductivityEstimated: Optional[float] = None
    waterIntakeMlTotal: Optional[float] = 0
    estimatedFluidLossMl: Optional[float] = 0
    estimatedNetHydrationBalanceMl: Optional[float] = 0
    sessionElapsedMin: Optional[float] = 0

    sweatState: str = "unknown"
    sweatEpisodeCount: Optional[int] = 0
    sweatEpisodeActiveDurationSec: Optional[float] = 0
    sweatEpisodeTotalDurationSec: Optional[float] = 0
    timeSinceLastSweatSec: Optional[float] = None
    longNoSweatAfterSweatingSec: Optional[float] = 0

    adcMean1Min: Optional[float] = None
    adcMean5Min: Optional[float] = None
    adcMean15Min: Optional[float] = None
    adcSlope1Min: Optional[float] = None
    adcSlope5Min: Optional[float] = None

    sensorDropoutCount: Optional[int] = 0
    sensorSignalLost: Optional[bool] = False


# Structure globale reçue par l’API: participantId + profile + measurements + derived.

class PredictPayload(BaseModel):
    participantId: str = Field(default="p001")
    profile: Profile
    measurements: Measurements
    derived: Derived




def safe_num(x, default=0.0):
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


# Encode Oui/Non en 1/0 pour les règles de décision.

def yes_no_to_num(v: Any) -> int:
    return 1 if str(v).strip().lower() == "oui" else 0


# Encode une activité qualitative en niveau numérique faible/moyen/élevé.

def encode_activity(v: str) -> int:
    s = str(v).strip().lower()
    if "élev" in s or "elev" in s:
        return 2
    if "mod" in s:
        return 1
    return 0


# Encode le niveau de mouvement d’air.

def encode_air(v: str) -> int:
    s = str(v).strip().lower()
    if "élev" in s or "elev" in s:
        return 2
    if "moy" in s:
        return 1
    return 0


# Encode le niveau d’habillement, utile pour majorer la charge thermique.

def encode_clothing(v: str) -> int:
    s = str(v).strip().lower()
    if "lourd" in s:
        return 2
    if "normal" in s:
        return 1
    if "léger" in s or "leger" in s:
        return 0
    return 1


# Encode le risque hydrique de base déclaré dans le profil.

def encode_hydration_risk(v: str) -> int:
    s = str(v).strip().lower()
    if "élev" in s or "elev" in s:
        return 2
    if "faible" in s:
        return 0
    return 1


# Encode la facilité d’accès à l’eau.

def encode_access_to_water(v: str) -> int:
    s = str(v).strip().lower()
    if "limité" in s or "limite" in s:
        return 2
    if "modéré" in s or "modere" in s:
        return 1
    return 0


# Calcule l’IMC à partir du poids et de la taille.

def compute_bmi(weight_kg: float, height_cm: float):
    w = safe_num(weight_kg, 0)
    h = safe_num(height_cm, 0) / 100.0
    if w <= 0 or h <= 0:
        return None
    return w / (h * h)


# Calcule l’indice de chaleur à partir de la température et de l’humidité relative.

def compute_heat_index_c(temp_c: float, hum_pct: float):
    T = safe_num(temp_c, None)
    R = safe_num(hum_pct, None)
    if T is None or R is None:
        return None

    Tf = T * 9 / 5 + 32
    HI = (
        -42.379
        + 2.04901523 * Tf
        + 10.14333127 * R
        - 0.22475541 * Tf * R
        - 0.00683783 * Tf * Tf
        - 0.05481717 * R * R
        + 0.00122874 * Tf * Tf * R
        + 0.00085282 * Tf * R * R
        - 0.00000199 * Tf * Tf * R * R
    )
    return (HI - 32) * 5 / 9


# Calcule le point de rosée, retourné dans les statistiques pour enrichir l’interprétation.

def compute_dew_point_c(temp_c: float, hum_pct: float):
    T = safe_num(temp_c, None)
    RH = safe_num(hum_pct, None)
    if T is None or RH is None or RH <= 0:
        return None
    a = 17.27
    b = 237.7
    alpha = ((a * T) / (b + T)) + math.log(RH / 100.0)
    return (b * alpha) / (a - alpha)


# Outils statistiques de base utilisés sur les fenêtres temporelles.

def std_or_zero(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    try:
        return float(statistics.pstdev(values))
    except Exception:
        return 0.0


# Moyenne protégée.

def mean_or_none(values: List[float]):
    if not values:
        return None
    return sum(values) / len(values)


# Médiane protégée.

def median_or_none(values: List[float]):
    if not values:
        return None
    try:
        return float(statistics.median(values))
    except Exception:
        return None


# Coefficient de variation = écart-type / moyenne, utilisé pour juger la variabilité du signal ADC.

def cv_or_zero(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean_or_none(values)
    s = std_or_zero(values)
    if m is None or abs(m) < 1e-9:
        return 0.0
    return s / abs(m)


# Pente linéaire d’une variable en fonction du temps, exprimée ici par minute.

def linear_slope(points: List[Dict[str, float]], key: str):
    vals = [(p["ts"], p[key]) for p in points if p.get(key) is not None]
    if len(vals) < 2:
        return 0.0

    t0 = vals[0][0]
    xs = [(t - t0) / 60.0 for t, _ in vals]
    ys = [y for _, y in vals]

    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n

    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))

    if den == 0:
        return 0.0
    return num / den


# Mesure l’écart d’une valeur courante par rapport à l’historique récent.

def zscore(current: float, values: List[float]):
    if current is None or not values:
        return 0.0
    m = mean_or_none(values)
    s = std_or_zero(values)
    if m is None or s == 0:
        return 0.0
    return (current - m) / s


# Analyse très simple du texte libre saisi dans le profil (conditions, ambiance, notes).
# Cette étape transforme certains mots-clés en drapeaux binaires utilisés par le score.

def parse_text_context(*texts: str):
    full = " ".join([t or "" for t in texts]).lower()

    def has_any(words: List[str]) -> int:
        return 1 if any(w in full for w in words) else 0

    parsed = {
        "text_heat": has_any(["chaleur", "hot", "chaud", "sauna", "canicule", "fournaise", "heat"]),
        "text_sun": has_any(["soleil", "sun", "ensoleillé", "plein soleil"]),
        "text_ppe": has_any(["équipement", "casque", "gants", "ppe", "protection", "habit lourd"]),
        "text_fatigue": has_any(["fatigue", "épuisé", "epuise", "tired", "faible", "weak"]),
        "text_rest": has_any(["pause", "repos", "rest", "break"]),
        "text_outdoor": has_any(["extérieur", "exterieur", "outside", "dehors"]),
        "text_humidity": has_any(["humide", "humidité", "humidite", "moite"]),
        "text_airflow": has_any(["vent", "ventilation", "ventilateur", "airflow"]),
        "text_sauna": has_any(["sauna"]),
        "text_exercise": has_any(["course", "running", "marche", "walking", "exercise", "sport", "effort"]),
    }

    matched_keywords = []
    keyword_map = {
        "text_heat": ["chaleur", "hot", "chaud", "sauna", "canicule", "heat"],
        "text_sun": ["soleil", "sun", "plein soleil"],
        "text_ppe": ["équipement", "ppe", "protection", "habit lourd"],
        "text_fatigue": ["fatigue", "épuisé", "epuise", "tired", "weak"],
        "text_rest": ["pause", "repos", "rest", "break"],
        "text_outdoor": ["extérieur", "exterieur", "outside", "dehors"],
        "text_humidity": ["humide", "humidité", "humidite", "moite"],
        "text_airflow": ["vent", "ventilation", "ventilateur", "airflow"],
        "text_sauna": ["sauna"],
        "text_exercise": ["course", "running", "marche", "walking", "exercise", "sport", "effort"],
    }

    for words in keyword_map.values():
        for w in words:
            if w in full:
                matched_keywords.append(w)

    parsed["matchedKeywords"] = sorted(list(set(matched_keywords)))
    parsed["rawText"] = full.strip()
    return parsed


# Récupère ou crée l’historique temporel d’un participant.

def get_history(participant_id: str):
    if participant_id not in SESSION_HISTORY:
        SESSION_HISTORY[participant_id] = deque(maxlen=MAX_HISTORY)
    return SESSION_HISTORY[participant_id]


# Ajoute un point dans l’historique pour permettre les statistiques glissantes 1, 5 et 15 min.

def add_history_point(participant_id: str, temp_c, hum_pct, adc_raw, touch_count, derived: Derived):
    hist = get_history(participant_id)
    hist.append({
        "ts": time.time(),
        "tempC": temp_c,
        "humPct": hum_pct,
        "adcRaw": adc_raw,
        "touchCount": touch_count,
        "sweatState": derived.sweatState,
        "adcMean1Min": derived.adcMean1Min,
        "adcMean5Min": derived.adcMean5Min,
        "adcMean15Min": derived.adcMean15Min,
        "adcSlope1Min": derived.adcSlope1Min,
        "adcSlope5Min": derived.adcSlope5Min,
        "sensorSignalLost": bool(derived.sensorSignalLost),
        "sensorDropoutCount": safe_num(derived.sensorDropoutCount, 0),
        "sweatEpisodeCount": safe_num(derived.sweatEpisodeCount, 0),
        "timeSinceLastSweatSec": safe_num(derived.timeSinceLastSweatSec, None),
        "longNoSweatAfterSweatingSec": safe_num(derived.longNoSweatAfterSweatingSec, 0),
    })
    return hist


# Extrait une fenêtre temporelle récente à partir de l’historique complet.

def filter_window(hist: deque, seconds: int) -> List[Dict[str, Any]]:
    now = time.time()
    return [x for x in hist if now - x["ts"] <= seconds]


# Construit les statistiques glissantes utilisées ensuite par la décision:
# moyennes, médianes, écarts-types, coefficients de variation, pentes et z-scores.

def compute_statistics(hist: deque, temp_c, hum_pct, adc_raw, touch_count, derived: Derived):
    w1 = filter_window(hist, 60)
    w5 = filter_window(hist, 5 * 60)
    w15 = filter_window(hist, 15 * 60)

    def vals(window, key):
        return [safe_num(x.get(key), None) for x in window if x.get(key) is not None]

    temp5 = vals(w5, "tempC")
    hum5 = vals(w5, "humPct")
    adc5 = vals(w5, "adcRaw")
    touch5 = vals(w5, "touchCount")

    temp15 = vals(w15, "tempC")
    hum15 = vals(w15, "humPct")
    adc15 = vals(w15, "adcRaw")

    stats = {
        "window1MinCount": len(w1),
        "window5MinCount": len(w5),
        "window15MinCount": len(w15),
        "tempMean5Min": mean_or_none(temp5),
        "humMean5Min": mean_or_none(hum5),
        "adcMean5MinFromHist": mean_or_none(adc5),
        "touchMean5Min": mean_or_none(touch5),
        "tempMedian5Min": median_or_none(temp5),
        "humMedian5Min": median_or_none(hum5),
        "adcMedian5Min": median_or_none(adc5),
        "tempStd5Min": std_or_zero(temp5),
        "humStd5Min": std_or_zero(hum5),
        "adcStd5Min": std_or_zero(adc5),
        "adcCv5Min": cv_or_zero(adc5),
        "tempSlope5Min": linear_slope(w5, "tempC"),
        "humSlope5Min": linear_slope(w5, "humPct"),
        "adcSlope5MinHist": linear_slope(w5, "adcRaw"),
        "touchSlope5Min": linear_slope(w5, "touchCount"),
        "tempZ15Min": zscore(temp_c, temp15),
        "humZ15Min": zscore(hum_pct, hum15),
        "adcZ15Min": zscore(adc_raw, adc15),
        "currentTempC": temp_c,
        "currentHumPct": hum_pct,
        "currentAdcRaw": adc_raw,
        "currentTouchCount": touch_count,
        "derivedAdcMean1Min": derived.adcMean1Min,
        "derivedAdcMean5Min": derived.adcMean5Min,
        "derivedAdcMean15Min": derived.adcMean15Min,
        "derivedAdcSlope1Min": derived.adcSlope1Min,
        "derivedAdcSlope5Min": derived.adcSlope5Min,
        "sensorSignalLost": bool(derived.sensorSignalLost),
        "sensorDropoutCount": safe_num(derived.sensorDropoutCount, 0),
        "sweatState": derived.sweatState,
        "sweatEpisodeCount": safe_num(derived.sweatEpisodeCount, 0),
        "sweatEpisodeActiveDurationSec": safe_num(derived.sweatEpisodeActiveDurationSec, 0),
        "sweatEpisodeTotalDurationSec": safe_num(derived.sweatEpisodeTotalDurationSec, 0),
        "timeSinceLastSweatSec": safe_num(derived.timeSinceLastSweatSec, None),
        "longNoSweatAfterSweatingSec": safe_num(derived.longNoSweatAfterSweatingSec, 0),
        "window5Min": w5,
    }
    return stats


# Met à jour une ligne de base progressive du participant quand les trois capteurs sont valides.
# Cette baseline sert ensuite à détecter une dérive par rapport à l’état habituel.

def update_baseline(participant_id: str, measurements: Measurements, derived: Derived):
    entry = BASELINE_STORE.get(participant_id)
    if entry is None:
        entry = {
            "count": 0,
            "tempMean": 0.0,
            "humMean": 0.0,
            "adcMean": 0.0,
        }
        BASELINE_STORE[participant_id] = entry

    temp = measurements.tempC
    hum = measurements.humPct
    adc = measurements.adcRaw

    valid_all = temp is not None and hum is not None and adc is not None and not derived.sensorSignalLost
    if not valid_all:
        return entry

    n = entry["count"] + 1
    entry["tempMean"] = ((entry["tempMean"] * entry["count"]) + temp) / n
    entry["humMean"] = ((entry["humMean"] * entry["count"]) + hum) / n
    entry["adcMean"] = ((entry["adcMean"] * entry["count"]) + adc) / n
    entry["count"] = n
    return entry


# Attribue un score de qualité des données sur 100.
# Des pénalités sont appliquées si des capteurs manquent, si le signal est perdu ou si l’historique est trop court.

def compute_data_quality(measurements: Measurements, derived: Derived, stats: Dict[str, Any]) -> Dict[str, Any]:
    score = 100.0
    flags = []

    if measurements.tempC is None:
        score -= 15
        flags.append("temp_missing")

    if measurements.humPct is None:
        score -= 15
        flags.append("hum_missing")

    if measurements.adcRaw is None:
        score -= 25
        flags.append("adc_missing")

    if derived.sensorSignalLost:
        score -= 30
        flags.append("sensor_signal_lost")

    if safe_num(derived.sensorDropoutCount, 0) >= 3:
        score -= 15
        flags.append("repeated_dropouts")

    if safe_num(stats.get("window5MinCount"), 0) < 10:
        score -= 10
        flags.append("short_history")

    adc_cv = safe_num(stats.get("adcCv5Min"), 0)
    if adc_cv == 0 and safe_num(stats.get("window5MinCount"), 0) >= 15 and safe_num(measurements.adcRaw, 0) == 0:
        score -= 10
        flags.append("adc_flatline")

    score = max(0.0, min(100.0, score))
    # Réponse JSON renvoyée au JavaScript pour mise à jour de l’interface utilisateur.
    return {
        "dataQualityScore": round(score, 1),
        "dataQualityFlags": flags,
        "signalReliable": score >= 70
    }


# Sous-score 1: stress thermique.
# Combine indice de chaleur, température, humidité, intensité d’activité, soleil, EPI, vêtements et contexte texte.

def compute_thermal_stress_score(profile: Profile, measurements: Measurements, derived: Derived, text_ctx: Dict[str, Any]) -> float:
    score = 0.0

    hi = safe_num(derived.heatIndexC, safe_num(measurements.tempC, 0))
    temp_c = safe_num(measurements.tempC, 0)
    hum_pct = safe_num(measurements.humPct, 0)

    score += max(0.0, hi - 26.0) * 2.2
    score += max(0.0, temp_c - 28.0) * 1.6
    score += max(0.0, hum_pct - 55.0) * 0.25

    score += 4.5 * safe_num(profile.activityIntensity, encode_activity(profile.activity))
    score += 5.0 * yes_no_to_num(profile.sunExposure)
    score += 4.0 * yes_no_to_num(profile.ppeUsed)
    score += 2.5 * yes_no_to_num(profile.indoorOutdoor == "Extérieur")
    score += 4.0 * text_ctx["text_heat"]
    score += 3.0 * text_ctx["text_sauna"]
    score += 2.0 * text_ctx["text_exercise"]
    score += 1.5 * encode_clothing(profile.clothingLevel)

    score -= 2.0 * encode_air(profile.airMovementLevel)
    score -= 2.0 * yes_no_to_num(profile.restBreakRecent)

    return max(0.0, min(100.0, score))


# Sous-score 2: déficit hydrique.
# Il dépend surtout du bilan hydrique net, de la durée d’exposition, de la cible de consommation et des facteurs médicaux.

def compute_fluid_deficit_score(profile: Profile, derived: Derived) -> float:
    balance = safe_num(derived.estimatedNetHydrationBalanceMl, 0)
    elapsed = safe_num(derived.sessionElapsedMin, 0)
    water = safe_num(derived.waterIntakeMlTotal, 0)
    target_hourly = safe_num(profile.targetHourlyIntakeMl, 0)
    age = safe_num(profile.age, 0)

    score = 0.0

    if balance < 0:
        score += min(45.0, abs(balance) / 35.0)

    if elapsed >= 60 and water <= 0:
        score += 8.0

    if elapsed >= 60 and target_hourly > 0:
        target_total = (elapsed / 60.0) * target_hourly
        deficit_vs_target = target_total - water
        if deficit_vs_target > 0:
            score += min(18.0, deficit_vs_target / 60.0)

    score += 5.0 * yes_no_to_num(profile.medicalRiskFlag)
    score += 4.0 * yes_no_to_num(profile.medicationRiskFlag)
    score += 4.0 * yes_no_to_num(profile.diureticUse)
    score += 2.5 * yes_no_to_num(profile.laxativeUse)
    score += 4.0 * yes_no_to_num(profile.vomitingDiarrheaToday)
    score += 3.0 * yes_no_to_num(profile.feverToday)
    score += 4.0 * yes_no_to_num(profile.diabetes)
    score += 4.0 * yes_no_to_num(profile.kidneyDisease)
    score += 3.5 * yes_no_to_num(profile.heartFailure)
    score += 3.5 * yes_no_to_num(profile.swallowingDifficulty)
    score += 3.0 * yes_no_to_num(profile.cognitiveRisk)

    if age >= 65:
        score += 6.0

    score += 3.0 * encode_hydration_risk(profile.baselineHydrationRisk)
    score += 2.0 * encode_access_to_water(profile.accessToWater)
    score += 2.0 * yes_no_to_num(profile.caregiverPrompting)

    return max(0.0, min(100.0, score))


# Sous-score 3: pattern de sudation.
# Cette fonction tente d’interpréter si la sudation est active, normale, absente, ou possiblement anormale.

def compute_sweat_pattern_score(derived: Derived, quality: Dict[str, Any]) -> (float, List[str], str):
    score = 0.0
    factors = []
    state = derived.sweatState or "unknown"

    if not quality["signalReliable"]:
        factors.append("Qualité du signal insuffisante pour interpréter la sudation")
        return 8.0, factors, "SIGNAL_UNCERTAIN"

    if state == "active":
        dur = safe_num(derived.sweatEpisodeActiveDurationSec, 0)
        score += min(20.0, dur / 60.0)
        factors.append("Sudation active détectée")
        return score, factors, "ACTIVE_SWEATING"

    long_no_sweat = safe_num(derived.longNoSweatAfterSweatingSec, 0)
    total_sweat = safe_num(derived.sweatEpisodeTotalDurationSec, 0)
    recent_gap = safe_num(derived.timeSinceLastSweatSec, 0)

    if total_sweat > 300 and long_no_sweat > 600:
        score += 24.0
        factors.append("Arrêt prolongé de la sudation après une phase de sudation")
        return score, factors, "POSSIBLE_ANHIDROSIS_OR_EXHAUSTION"

    if recent_gap > 900 and total_sweat > 0:
        score += 10.0
        factors.append("Absence de sudation confirmée récemment après activité antérieure")
        return score, factors, "NO_SWEAT_CONFIRMED"

    factors.append("Pattern sudoral non alarmant")
    return score, factors, "RECOVERY_OR_BASELINE"


# Bonus additionnel si les tendances récentes montent rapidement.

def compute_temporal_bonus(stats: Dict[str, Any], derived: Derived) -> float:
    bonus = 0.0
    bonus += max(0.0, safe_num(stats.get("tempSlope5Min"), 0)) * 3.0
    bonus += max(0.0, safe_num(stats.get("humSlope5Min"), 0)) * 0.20
    bonus += max(0.0, safe_num(derived.adcSlope5Min, safe_num(stats.get("adcSlope5MinHist"), 0))) * 0.05
    return min(15.0, bonus)


# Bonus si les mesures actuelles dépassent la ligne de base propre au participant.

def compute_baseline_deviation_bonus(baseline: Dict[str, Any], measurements: Measurements) -> float:
    if not baseline or safe_num(baseline.get("count"), 0) < 20:
        return 0.0

    bonus = 0.0
    temp_mean = safe_num(baseline.get("tempMean"), None)
    hum_mean = safe_num(baseline.get("humMean"), None)
    adc_mean = safe_num(baseline.get("adcMean"), None)

    if temp_mean is not None and measurements.tempC is not None:
        bonus += max(0.0, measurements.tempC - temp_mean) * 1.5

    if hum_mean is not None and measurements.humPct is not None:
        bonus += max(0.0, measurements.humPct - hum_mean) * 0.08

    if adc_mean is not None and measurements.adcRaw is not None:
        bonus += max(0.0, measurements.adcRaw - adc_mean) * 0.015

    return min(12.0, bonus)


# Partie pseudo-ML/statistique du modèle.
# On construit une somme pondérée x puis on applique une sigmoïde pour obtenir une probabilité entre 0 et 1.

def compute_statistical_probability(profile: Profile, measurements: Measurements, derived: Derived,
                                    stats: Dict[str, Any], text_ctx: Dict[str, Any], quality: Dict[str, Any]) -> float:
    x = 0.0
    x += 0.10 * safe_num(derived.heatIndexC, safe_num(measurements.tempC, 0))
    x += 0.025 * max(0.0, safe_num(measurements.humPct, 0) - 50.0)
    x += 0.018 * max(0.0, -safe_num(derived.estimatedNetHydrationBalanceMl, 0))
    x += 0.35 * max(0.0, safe_num(stats.get("tempSlope5Min"), 0))
    x += 0.05 * max(0.0, safe_num(derived.adcSlope5Min, 0))
    x += 0.025 * safe_num(derived.adcMean5Min, safe_num(measurements.adcRaw, 0))
    x += 0.08 * safe_num(profile.activityIntensity, encode_activity(profile.activity))
    x += 0.20 * yes_no_to_num(profile.medicalRiskFlag)
    x += 0.15 * yes_no_to_num(profile.medicationRiskFlag)
    x += 0.15 * yes_no_to_num(profile.diureticUse)
    x += 0.12 * yes_no_to_num(profile.diabetes)
    x += 0.10 * yes_no_to_num(profile.kidneyDisease)
    x += 0.18 * yes_no_to_num(profile.sunExposure)
    x += 0.16 * yes_no_to_num(profile.ppeUsed)
    x += 0.14 * text_ctx["text_heat"]
    x += 0.16 * text_ctx["text_sauna"]
    x += 0.10 * text_ctx["text_fatigue"]
    x += 0.10 * encode_hydration_risk(profile.baselineHydrationRisk)

    if derived.sweatState == "active":
        x += 0.30
    if safe_num(derived.longNoSweatAfterSweatingSec, 0) > 600:
        x += 0.35
    if not quality["signalReliable"]:
        x -= 0.25

    x -= 3.25

    prob = 1.0 / (1.0 + math.exp(-x))
    return max(0.0, min(1.0, prob))


def compute_trained_ml_probability(measurements: Measurements, derived: Derived):
    if ML_MODEL is None or ML_SCALER is None or ML_FEATURES is None:
        return None

    try:
        feature_row = {
            "tempC": safe_num(measurements.tempC, 0),
            "humPct": safe_num(measurements.humPct, 0),
            "adcRaw": safe_num(measurements.adcRaw, 0),
            "estimatedNetHydrationBalanceMl": safe_num(derived.estimatedNetHydrationBalanceMl, 0),
            "sessionElapsedMin": safe_num(derived.sessionElapsedMin, 0),
        }

        X = pd.DataFrame([feature_row])
        X = X.reindex(columns=ML_FEATURES, fill_value=0)
        X_scaled = ML_SCALER.transform(X)
        prob = float(ML_MODEL.predict_proba(X_scaled)[0, 1])
        return max(0.0, min(1.0, prob))
    except Exception:
        return None


# Coeur de l’algorithme décisionnel.
# Il fusionne les règles explicites, les tendances temporelles, la comparaison à la baseline et une probabilité statistique.

def compute_hybrid_decision(participant_id: str, profile: Profile, measurements: Measurements, derived: Derived,
                            stats: Dict[str, Any], text_ctx: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    # 1) On évalue d’abord si les données sont suffisamment fiables pour être interprétées.
    quality = compute_data_quality(measurements, derived, stats)

    # 2) On calcule les trois grands sous-scores du modèle.
    thermal = compute_thermal_stress_score(profile, measurements, derived, text_ctx)
    fluid = compute_fluid_deficit_score(profile, derived)
    sweat_score, sweat_factors, physiologic_state = compute_sweat_pattern_score(derived, quality)

    # 3) On ajoute des bonifications liées à la dynamique récente et à la déviation par rapport à la baseline.
    temporal_bonus = compute_temporal_bonus(stats, derived)
    baseline_bonus = compute_baseline_deviation_bonus(baseline, measurements)
    prob_rules = compute_statistical_probability(profile, measurements, derived, stats, text_ctx, quality)
    prob_trained_ml = compute_trained_ml_probability(measurements, derived)

    if prob_trained_ml is None:
        prob_final = prob_rules
        trained_ml_score_100 = None
    else:
        prob_final = 0.70 * prob_rules + 0.30 * prob_trained_ml
        trained_ml_score_100 = prob_trained_ml * 100.0

    ml_score_100 = prob_final * 100.0

    # 4) Score explicable basé sur des règles pondérées.
    rule_score = 0.35 * thermal + 0.40 * fluid + 0.25 * sweat_score + temporal_bonus + baseline_bonus
    # 5) Fusion hybride entre le score par règles et la probabilité statistique convertie sur 100.
    final_score = 0.62 * rule_score + 0.38 * ml_score_100

    # 6) Si les données sont mauvaises, on réduit le score final.
    if quality["dataQualityScore"] < 50:
        final_score *= 0.85

    # 7) Cas spécial: arrêt prolongé de la sudation sous chaleur, donc pénalité supplémentaire.
    if physiologic_state == "POSSIBLE_ANHIDROSIS_OR_EXHAUSTION" and thermal >= 20:
        final_score += 12

    final_score = max(0.0, min(100.0, final_score))

    # 8) Conversion du score numérique vers une classe de risque.
    if final_score >= 75:
        level = "CRITICAL"
    elif final_score >= 52:
        level = "HIGH"
    elif final_score >= 28:
        level = "MEDIUM"
    else:
        level = "LOW"

    # 9) La confiance augmente avec la qualité des données, la longueur d’historique et l’éloignement de la probabilité par rapport à 0.5.
    hist_len = len(get_history(participant_id))
    confidence = (
        0.30
        + 0.45 * (quality["dataQualityScore"] / 100.0)
        + 0.15 * min(1.0, hist_len / 120.0)
        + 0.10 * abs(prob_final - 0.5) * 2.0
    )
    confidence = max(0.35, min(0.98, confidence))

    # 10) Génération d’explications textuelles pour la page web.
    main_factors = []
    if thermal >= 20:
        main_factors.append(f"Stress thermique élevé ({thermal:.1f}/100)")
    if fluid >= 20:
        main_factors.append(f"Déficit hydrique probable ({fluid:.1f}/100)")
    main_factors.extend(sweat_factors)

    if safe_num(derived.estimatedNetHydrationBalanceMl, 0) < 0:
        main_factors.append(f"Bilan hydrique net négatif ({round(safe_num(derived.estimatedNetHydrationBalanceMl, 0))} mL)")

    if quality["dataQualityFlags"]:
        main_factors.append("Qualité données: " + ", ".join(quality["dataQualityFlags"]))

    # Réponse JSON renvoyée au JavaScript pour mise à jour de l’interface utilisateur.
    return {
        "riskLevel": level,
        "riskScore": round(final_score, 1),
        "riskProbability": round(prob_final, 4),
        "confidence": round(confidence, 3),
        "physiologicState": physiologic_state,
        "subscores": {
            "thermalStress": round(thermal, 1),
            "fluidDeficit": round(fluid, 1),
            "sweatPattern": round(sweat_score, 1),
            "ruleScore": round(rule_score, 1),
            "mlScore100": round(ml_score_100, 1),
            "ruleProbScore100": round(prob_rules * 100.0, 1),
            "trainedMlScore100": None if trained_ml_score_100 is None else round(trained_ml_score_100, 1),
            "temporalBonus": round(temporal_bonus, 2),
            "baselineBonus": round(baseline_bonus, 2),
        },
        "quality": quality,
        "mainFactors": main_factors[:8] if main_factors else ["Surveillance standard"],
        "mlDebug": {
            "ruleProbability": round(prob_rules, 4),
            "trainedMlProbability": None if prob_trained_ml is None else round(prob_trained_ml, 4),
            "trainedMlLoaded": ML_MODEL is not None and ML_SCALER is not None and ML_FEATURES is not None,
            "trainedMlError": ML_MODEL_ERROR,
        },
    }


# Traduit le niveau de risque et l’état physiologique en consigne d’action.

def recommended_action_v2(level: str, state: str, hydration_balance_ml: float, quality: Dict[str, Any]) -> str:
    if not quality.get("signalReliable", True):
        return "Vérifier le contact capteur et confirmer les mesures avant décision clinique."

    if level == "CRITICAL" and state == "POSSIBLE_ANHIDROSIS_OR_EXHAUSTION":
        return "Alerte critique: exposition thermique avec arrêt prolongé de la sudation. Cesser l’exposition, refroidir, hydrater et évaluer immédiatement."

    if level == "CRITICAL":
        return "Alerte critique: arrêter l’activité, refroidir, hydrater rapidement et réévaluer immédiatement."

    if level == "HIGH":
        return "Risque élevé: pause immédiate, eau, ombre ou refroidissement, puis réévaluation rapprochée."

    if level == "MEDIUM":
        return "Risque modéré: encourager l’hydratation, réduire l’exposition et surveiller l’évolution."

    return "Surveillance standard."


# Crée les fichiers CSV de sortie s’ils n’existent pas encore.

def ensure_csv(path: Path, fieldnames: List[str]):
    if path.exists():
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()


# Met à jour le fichier maître contenant le profil du participant.

def upsert_master_row(participant_id: str, profile: Profile):
    fieldnames = [
        "participant_id", "full_name", "age", "sex", "weight_kg", "height_cm", "bmi",
        "activity", "fitness_level", "acclimatization", "history_heat_illness",
        "medical_risk_flag", "medication_risk_flag", "indoor_outdoor", "sun_exposure",
        "air_movement_level", "ppe_used", "clothing_level", "activity_intensity",
        "water_ml_per_press", "rest_break_recent", "kidney_disease", "diabetes",
        "heart_failure", "cognitive_risk", "swallowing_difficulty", "diuretic_use",
        "laxative_use", "usual_daily_water_intake_ml", "target_hourly_intake_ml",
        "access_to_water", "caregiver_prompting", "baseline_hydration_risk",
        "planned_exposure_duration_min", "ambient", "conditions", "notes",
        "created_at", "updated_at"
    ]
    ensure_csv(MASTER_CSV, fieldnames)

    rows = []
    now_iso = datetime.now(timezone.utc).isoformat()

    existing = False
    if MASTER_CSV.exists():
        with open(MASTER_CSV, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["participant_id"] == participant_id:
                    existing = True
                    created_at = row.get("created_at") or now_iso
                    row = {
                        "participant_id": participant_id,
                        "full_name": profile.fullName,
                        "age": safe_num(profile.age, 0),
                        "sex": profile.sex,
                        "weight_kg": safe_num(profile.weightKg, 0),
                        "height_cm": safe_num(profile.heightCm, 0),
                        "bmi": compute_bmi(profile.weightKg, profile.heightCm),
                        "activity": profile.activity,
                        "fitness_level": profile.fitnessLevel,
                        "acclimatization": profile.acclimatization,
                        "history_heat_illness": profile.historyHeatIllness,
                        "medical_risk_flag": profile.medicalRiskFlag,
                        "medication_risk_flag": profile.medicationRiskFlag,
                        "indoor_outdoor": profile.indoorOutdoor,
                        "sun_exposure": profile.sunExposure,
                        "air_movement_level": profile.airMovementLevel,
                        "ppe_used": profile.ppeUsed,
                        "clothing_level": profile.clothingLevel,
                        "activity_intensity": safe_num(profile.activityIntensity, 0),
                        "water_ml_per_press": safe_num(profile.waterMlPerPress, 0),
                        "rest_break_recent": profile.restBreakRecent,
                        "kidney_disease": profile.kidneyDisease,
                        "diabetes": profile.diabetes,
                        "heart_failure": profile.heartFailure,
                        "cognitive_risk": profile.cognitiveRisk,
                        "swallowing_difficulty": profile.swallowingDifficulty,
                        "diuretic_use": profile.diureticUse,
                        "laxative_use": profile.laxativeUse,
                        "usual_daily_water_intake_ml": safe_num(profile.usualDailyWaterIntakeMl, 0),
                        "target_hourly_intake_ml": safe_num(profile.targetHourlyIntakeMl, 0),
                        "access_to_water": profile.accessToWater,
                        "caregiver_prompting": profile.caregiverPrompting,
                        "baseline_hydration_risk": profile.baselineHydrationRisk,
                        "planned_exposure_duration_min": safe_num(profile.plannedExposureDurationMin, 0),
                        "ambient": profile.ambient,
                        "conditions": profile.conditions,
                        "notes": profile.notes,
                        "created_at": created_at,
                        "updated_at": now_iso,
                    }
                rows.append(row)

    if not existing:
        rows.append({
            "participant_id": participant_id,
            "full_name": profile.fullName,
            "age": safe_num(profile.age, 0),
            "sex": profile.sex,
            "weight_kg": safe_num(profile.weightKg, 0),
            "height_cm": safe_num(profile.heightCm, 0),
            "bmi": compute_bmi(profile.weightKg, profile.heightCm),
            "activity": profile.activity,
            "fitness_level": profile.fitnessLevel,
            "acclimatization": profile.acclimatization,
            "history_heat_illness": profile.historyHeatIllness,
            "medical_risk_flag": profile.medicalRiskFlag,
            "medication_risk_flag": profile.medicationRiskFlag,
            "indoor_outdoor": profile.indoorOutdoor,
            "sun_exposure": profile.sunExposure,
            "air_movement_level": profile.airMovementLevel,
            "ppe_used": profile.ppeUsed,
            "clothing_level": profile.clothingLevel,
            "activity_intensity": safe_num(profile.activityIntensity, 0),
            "water_ml_per_press": safe_num(profile.waterMlPerPress, 0),
            "rest_break_recent": profile.restBreakRecent,
            "kidney_disease": profile.kidneyDisease,
            "diabetes": profile.diabetes,
            "heart_failure": profile.heartFailure,
            "cognitive_risk": profile.cognitiveRisk,
            "swallowing_difficulty": profile.swallowingDifficulty,
            "diuretic_use": profile.diureticUse,
            "laxative_use": profile.laxativeUse,
            "usual_daily_water_intake_ml": safe_num(profile.usualDailyWaterIntakeMl, 0),
            "target_hourly_intake_ml": safe_num(profile.targetHourlyIntakeMl, 0),
            "access_to_water": profile.accessToWater,
            "caregiver_prompting": profile.caregiverPrompting,
            "baseline_hydration_risk": profile.baselineHydrationRisk,
            "planned_exposure_duration_min": safe_num(profile.plannedExposureDurationMin, 0),
            "ambient": profile.ambient,
            "conditions": profile.conditions,
            "notes": profile.notes,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

    with open(MASTER_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# Ajoute une ligne événementielle à chaque prédiction pour journaliser l’évolution dans le temps.

def append_event_row(participant_id: str, profile: Profile, measurements: Measurements, derived: Derived,
                     decision: Dict[str, Any], stats: Dict[str, Any]):
    fieldnames = [
        "ts", "participant_id", "temp_c", "hum_pct", "adc_raw", "touch", "touch_count",
        "led", "led_jaune", "buzzer", "ac_enabled", "ac_signal_freq_hz", "ac_instant_v",
        "bmi", "heat_index_c", "conductivity_estimated", "water_intake_ml_total",
        "estimated_fluid_loss_ml", "estimated_net_hydration_balance_ml", "session_elapsed_min",
        "sweat_state", "sweat_episode_count", "sweat_episode_active_duration_sec",
        "sweat_episode_total_duration_sec", "time_since_last_sweat_sec",
        "long_no_sweat_after_sweating_sec", "adc_mean_1min", "adc_mean_5min", "adc_mean_15min",
        "adc_slope_1min", "adc_slope_5min", "sensor_dropout_count", "sensor_signal_lost",
        "risk_level", "risk_score", "risk_probability", "confidence", "physiologic_state",
        "thermal_stress_score", "fluid_deficit_score", "sweat_pattern_score",
        "data_quality_score", "recommended_action"
    ]
    ensure_csv(EVENTS_CSV, fieldnames)

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "participant_id": participant_id,
        "temp_c": measurements.tempC,
        "hum_pct": measurements.humPct,
        "adc_raw": measurements.adcRaw,
        "touch": measurements.touch,
        "touch_count": measurements.touchCount,
        "led": measurements.led,
        "led_jaune": measurements.led_jaune,
        "buzzer": measurements.buzzer,
        "ac_enabled": measurements.acEnabled,
        "ac_signal_freq_hz": measurements.acSignalFreqHz,
        "ac_instant_v": measurements.acInstantV,
        "bmi": derived.bmi,
        "heat_index_c": derived.heatIndexC,
        "conductivity_estimated": derived.conductivityEstimated,
        "water_intake_ml_total": derived.waterIntakeMlTotal,
        "estimated_fluid_loss_ml": derived.estimatedFluidLossMl,
        "estimated_net_hydration_balance_ml": derived.estimatedNetHydrationBalanceMl,
        "session_elapsed_min": derived.sessionElapsedMin,
        "sweat_state": derived.sweatState,
        "sweat_episode_count": derived.sweatEpisodeCount,
        "sweat_episode_active_duration_sec": derived.sweatEpisodeActiveDurationSec,
        "sweat_episode_total_duration_sec": derived.sweatEpisodeTotalDurationSec,
        "time_since_last_sweat_sec": derived.timeSinceLastSweatSec,
        "long_no_sweat_after_sweating_sec": derived.longNoSweatAfterSweatingSec,
        "adc_mean_1min": derived.adcMean1Min,
        "adc_mean_5min": derived.adcMean5Min,
        "adc_mean_15min": derived.adcMean15Min,
        "adc_slope_1min": derived.adcSlope1Min,
        "adc_slope_5min": derived.adcSlope5Min,
        "sensor_dropout_count": derived.sensorDropoutCount,
        "sensor_signal_lost": derived.sensorSignalLost,
        "risk_level": decision["riskLevel"],
        "risk_score": decision["riskScore"],
        "risk_probability": decision["riskProbability"],
        "confidence": decision["confidence"],
        "physiologic_state": decision["physiologicState"],
        "thermal_stress_score": decision["subscores"]["thermalStress"],
        "fluid_deficit_score": decision["subscores"]["fluidDeficit"],
        "sweat_pattern_score": decision["subscores"]["sweatPattern"],
        "data_quality_score": decision["quality"]["dataQualityScore"],
        "recommended_action": decision["recommendedAction"],
    }

    with open(EVENTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(row)


# Endpoint simple pour vérifier que l’API Python tourne.

@app.get("/health")
def health():
    return {"ok": True, "service": "Bracelet Decision API v2"}


# Endpoint principal appelé par le JavaScript via fetch.
# Le JS prépare profile, measurements et derived puis les envoie ici à chaque cycle de rafraîchissement.

@app.post("/predict")
def predict(data: PredictPayload):
    # Identifiant utilisé pour séparer l’historique et la baseline de chaque participant.
    participant_id = data.participantId or "p001"

    # On récupère les trois blocs envoyés par le front-end JS.
    profile = data.profile
    measurements = data.measurements
    derived = data.derived

    temp_c = measurements.tempC
    hum_pct = measurements.humPct
    adc_raw = measurements.adcRaw
    touch_count = safe_num(measurements.touchCount, 0)

    # Si certaines variables dérivées n’ont pas été calculées côté JS, Python les complète.
    if derived.bmi is None:
        derived.bmi = compute_bmi(profile.weightKg, profile.heightCm)

    if derived.heatIndexC is None and temp_c is not None and hum_pct is not None:
        derived.heatIndexC = compute_heat_index_c(temp_c, hum_pct)

    dew_point_c = compute_dew_point_c(temp_c, hum_pct) if temp_c is not None and hum_pct is not None else None

    # Mise à jour de la mémoire temporelle, puis calcul des statistiques glissantes et du contexte texte.
    hist = add_history_point(participant_id, temp_c, hum_pct, adc_raw, touch_count, derived)
    stats = compute_statistics(hist, temp_c, hum_pct, adc_raw, touch_count, derived)
    text_ctx = parse_text_context(profile.conditions, profile.ambient, profile.notes)

    # Mise à jour de la baseline individuelle.
    baseline = update_baseline(participant_id, measurements, derived)

    # Appel du moteur décisionnel principal.
    decision = compute_hybrid_decision(
        participant_id=participant_id,
        profile=profile,
        measurements=measurements,
        derived=derived,
        stats=stats,
        text_ctx=text_ctx,
        baseline=baseline
    )

    # Le bilan hydrique net envoyé à la page web est repris tel quel depuis derived.
    hydration_balance_ml = round(safe_num(derived.estimatedNetHydrationBalanceMl, 0))
    action = recommended_action_v2(
        decision["riskLevel"],
        decision["physiologicState"],
        hydration_balance_ml,
        decision["quality"]
    )
    decision["recommendedAction"] = action

    # Journalisation du profil et de l’événement courant dans les CSV.
    upsert_master_row(participant_id, profile)
    append_event_row(participant_id, profile, measurements, derived, decision, stats)

    # Réponse JSON renvoyée au JavaScript pour mise à jour de l’interface utilisateur.
    return {
        "participantId": participant_id,
        "riskLevel": decision["riskLevel"],
        "riskScore": decision["riskScore"],
        "riskProbability": decision["riskProbability"],
        "confidence": decision["confidence"],
        "hydrationBalanceMl": hydration_balance_ml,
        "mainFactors": decision["mainFactors"],
        "recommendedAction": decision["recommendedAction"],
        "physiologicState": decision["physiologicState"],
        "subscores": decision["subscores"],
        "quality": decision["quality"],
        "mlDebug": decision["mlDebug"],
        "statistics": {
            **stats,
            "dewPointC": dew_point_c,
            "bmi": derived.bmi,
            "heatIndexC": derived.heatIndexC,
            "baseline": baseline,
        },
        "parsedContext": text_ctx,
        "debug": {
            "historyLength": len(hist),
            "participantId": participant_id,
        }
    }
