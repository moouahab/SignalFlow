"""Opérations ML communes aux exécutions locale et ZenML, sans tracking."""
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def build_model(training):
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median', keep_empty_features=True)),
        ('scaler', StandardScaler()),
        ('classifier', LogisticRegression(**training['model']['parameters'],
            random_state=training['seed'], solver='lbfgs')),
    ])


def preprocess(model, dataset, split):
    return model[:-1].fit_transform(dataset.X[split.train], dataset.y[split.train])


def fit(model, transformed, dataset, split):
    model[-1].fit(transformed, dataset.y[split.train])


def evaluate(model, dataset, indices, prefix):
    expected = dataset.y[indices]
    predicted = model.predict(dataset.X[indices])
    precision, recall, f1, _ = precision_recall_fscore_support(
        expected, predicted, average='macro', labels=model.classes_, zero_division=0)
    return {f'{prefix}_accuracy': float(accuracy_score(expected, predicted)),
        f'{prefix}_precision_macro': float(precision), f'{prefix}_recall_macro': float(recall),
        f'{prefix}_f1_macro': float(f1)}
