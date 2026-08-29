import os

def save_model(model, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        model.save_model(path)
    except Exception as e:
        raise RuntimeError(f"Could not save model to {path}: {e}")
