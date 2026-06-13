import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics import classification_report, accuracy_score
from lightgbm import LGBMClassifier

# ==========================================
# 1. DATA LOADING & INITIAL CLEANING
# ==========================================
print("Loading and cleaning data...")
file_path = "path_to_your_dataset.csv"  # <-- CHANGE THIS TO YOUR FILE PATH
df = pd.read_csv(file_path)

# Fix typos and drop junk columns
df["specific.disorder"] = df["specific.disorder"].replace({"Obsessive compulsitve disorder": "Obsessive compulsive disorder"})
df = df.drop(columns=["no.", "eeg.date", "Unnamed: 122"], errors="ignore")

# Impute missing values (Education and IQ) grouped by disorder
df["education"] = df.groupby("main.disorder")["education"].transform(lambda x: x.fillna(x.median()))
df["IQ"] = df.groupby("main.disorder")["IQ"].transform(lambda x: x.fillna(x.median()))

# ==========================================
# 2. FILTER PATIENTS & ENCODE METADATA
# ==========================================
print("Isolating patient cohort and encoding targets...")
# We are building the Differential Diagnosis engine, so we drop Healthy Controls
df_patients = df[df["main.disorder"] != "Healthy control"].copy().reset_index(drop=True)

# Encode Sex (M -> 1, F -> 0)
df_patients["sex"] = df_patients["sex"].map({"M": 1, "F": 0})

# Encode Target Labels (6 Psychiatric Disorders)
le_target = LabelEncoder()
y = le_target.fit_transform(df_patients["main.disorder"])
target_classes = le_target.classes_.tolist()

# ==========================================
# 3. FEATURE ENGINEERING: CLINICAL RATIOS
# ==========================================
print("Engineering clinical EEG biomarkers (Frontal Ratios)...")
frontal_electrodes = ["FP1", "FP2", "F3", "F4", "Fz", "F7", "F8"]

# Extract columns for specific frequency bands
theta_cols = [c for c in df_patients.columns if "AB." in c and ".theta." in c and any(e in c for e in frontal_electrodes)]
beta_cols  = [c for c in df_patients.columns if "AB." in c and ".beta."  in c and any(e in c for e in frontal_electrodes)]
alpha_cols = [c for c in df_patients.columns if "AB." in c and ".alpha." in c and any(e in c for e in frontal_electrodes)]

# Calculate Frontal averages
frontal_theta = df_patients[theta_cols].mean(axis=1)
frontal_beta  = df_patients[beta_cols].mean(axis=1)
frontal_alpha = df_patients[alpha_cols].mean(axis=1)

# Add Ratios (Adding 1e-6 to avoid divide-by-zero)
df_patients["Ratio_Theta_Beta"]  = frontal_theta / (frontal_beta + 1e-6)
df_patients["Ratio_Theta_Alpha"] = frontal_theta / (frontal_alpha + 1e-6)
df_patients["Ratio_Alpha_Beta"]  = frontal_alpha / (frontal_beta + 1e-6)

# ==========================================
# 4. FEATURE SEPARATION & TRAIN/TEST SPLIT
# ==========================================
print("Splitting dataset into 85% Train / 15% Test...")
metadata_cols = ["sex", "age", "education", "IQ"]
ratio_cols = ["Ratio_Theta_Beta", "Ratio_Theta_Alpha", "Ratio_Alpha_Beta"]
ab_cols = [c for c in df_patients.columns if "AB." in c]
coh_cols = [c for c in df_patients.columns if "COH." in c]

# Create our pre-split feature matrix (Everything except the target strings)
X_all = df_patients[metadata_cols + ratio_cols + ab_cols + coh_cols]

# Stratified Split
X_train, X_test, y_train, y_test = train_test_split(
    X_all, y, test_size=0.15, random_state=42, stratify=y
)

# ==========================================
# 5. NETWORK COMPRESSION (COHERENCE -> PCA)
# ==========================================
print("Compressing 1,000+ Coherence features into 15 Principal Components...")
# We must scale and fit PCA ONLY on the training data to prevent leakage!
scaler_coh = StandardScaler()
pca = PCA(n_components=15, random_state=42)

# Fit/Transform Training Data
coh_train_scaled = scaler_coh.fit_transform(X_train[coh_cols])
coh_train_pca = pca.fit_transform(coh_train_scaled)

# Transform Testing Data
coh_test_scaled = scaler_coh.transform(X_test[coh_cols])
coh_test_pca = pca.transform(coh_test_scaled)

# ==========================================
# 6. ASSEMBLE FINAL OPTIMIZED MATRICES
# ==========================================
print("Assembling final feature matrices...")
def build_final_matrix(X_base, coh_pca_array):
    """Combines Metadata, Ratios, AB Powers, and new PCA components."""
    # Reset indices to ensure safe concatenation
    df_base = X_base[metadata_cols + ratio_cols + ab_cols].copy().reset_index(drop=True)
    df_pca = pd.DataFrame(
        coh_pca_array, 
        columns=[f"COH_PCA_{i}" for i in range(coh_pca_array.shape[1])]
    )
    return pd.concat([df_base, df_pca], axis=1)

X_train_final = build_final_matrix(X_train, coh_train_pca)
X_test_final  = build_final_matrix(X_test, coh_test_pca)

print(f"Final feature count reduced from 1,149 down to {X_train_final.shape[1]}")

# ==========================================
# 7. MODEL TRAINING (LightGBM)
# ==========================================
print("\nTraining LightGBM Multi-Class Model...")
lgb_model = LGBMClassifier(
    n_estimators=200,
    max_depth=5,              
    learning_rate=0.02,       
    class_weight='balanced',  
    subsample=0.8,            
    colsample_bytree=0.8,     
    random_state=42,
    verbosity=-1,
    n_jobs=-1
)

lgb_model.fit(X_train_final, y_train)

# ==========================================
# 8. EVALUATION
# ==========================================
print("\nEvaluating on Hidden Test Set...")
y_test_pred = lgb_model.predict(X_test_final)
final_accuracy = accuracy_score(y_test, y_test_pred)

print(f"\n{'='*50}")
print(f"FINAL HIDDEN TEST ACCURACY: {final_accuracy:.4f}")
print(f"{'='*50}\n")

print(classification_report(y_test, y_test_pred, target_names=target_classes))