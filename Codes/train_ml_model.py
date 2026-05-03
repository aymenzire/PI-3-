import json
import joblib
import pandas as pd
import matplotlib.pyplot as plt

from pathlib import Path




from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler


from sklearn.linear_model import LogisticRegression

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    ConfusionMatrixDisplay,
    roc_curve,
    roc_auc_score,
    classification_report
)


# 1. CONFIGURATION
CSV_FILE = "test_data_bracelet.csv"
OUTPUT_DIR = Path("ml_saved_model")
OUTPUT_DIR.mkdir(exist_ok=True)

MODEL_FILE = OUTPUT_DIR / "bracelet_logreg_model.joblib"
SCALER_FILE = OUTPUT_DIR / "bracelet_scaler.joblib"
FEATURES_FILE = OUTPUT_DIR / "bracelet_features.json"




# 2. CHARGEMENT DES DONNÉES


df = pd.read_csv(CSV_FILE)

print("Colonnes disponibles:")
print(df.columns.tolist())


# 3. PRÉPARATION DE LA CIBLE

def label_binary(x):
    x = str(x).upper().strip()
    return 1 if x in ["HIGH", "CRITICAL"] else 0

df["target"] = df["truthLabel"].apply(label_binary)


# 4.CHOIX DES FEATURES


features = [
    "tempC",
    "humPct",
    "adcRaw",
    "estimatedNetHydrationBalanceMl",
    "sessionElapsedMin"
]

X = df[features].fillna(0)
y = df["target"]


# 5. SPLIT TRAIN / TEST
X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.30,
    random_state=42,
    stratify=y
)



# 6. NORMALISATION


scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)


# 7. ENTRAÎNEMENT DU MODÈLE


model = LogisticRegression(max_iter=2000, random_state=42)
model.fit(X_train_scaled, y_train)


# 8. PRÉDICTIONS


y_pred = model.predict(X_test_scaled)
y_prob = model.predict_proba(X_test_scaled)[:, 1]


# 9. MÉTRIQUES


accuracy = accuracy_score(y_test, y_pred)
auc = roc_auc_score(y_test, y_prob)

print("\nAccuracy:", round(accuracy, 4))
print("AUC:", round(auc, 4))

print("\nClassification report:")
print(classification_report(y_test, y_pred, digits=4))


# 10. MATRICE DE CONFUSION

cm = confusion_matrix(y_test, y_pred)

plt.figure(figsize=(6, 5))
disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=["Négatif", "Positif"])
disp.plot(ax=plt.gca(), values_format="d", colorbar=False)
plt.title("Matrice de confusion")
plt.tight_layout()
plt.show()


# 11. COURBE ROC


fpr, tpr, _ = roc_curve(y_test, y_prob)

plt.figure(figsize=(6, 5))
plt.plot(fpr, tpr, label=f"AUC = {auc:.3f}")
plt.plot([0, 1], [0, 1], linestyle="--")
plt.xlabel("Faux positifs")
plt.ylabel("Vrais positifs")
plt.title("Courbe ROC")
plt.legend()
plt.tight_layout()
plt.show()








# 12. COEFFICIENTS DU MODÈLE


print("\nCoefficients du modèle:")
for name, coef in zip(features, model.coef_[0]):
    print(f"{name}: {coef:.4f}")

print("\nIntercept:")
print(model.intercept_[0])


# 13. SAUVEGARDE DU MODÈLE
joblib.dump(model, MODEL_FILE)
joblib.dump(scaler, SCALER_FILE)

with open(FEATURES_FILE, "w", encoding="utf-8") as f:
    json.dump(features, f, ensure_ascii=False, indent=2)

print("\nModèle sauvegardé avec succès:")
print(" -", MODEL_FILE)
print(" -", SCALER_FILE)
print(" -", FEATURES_FILE)