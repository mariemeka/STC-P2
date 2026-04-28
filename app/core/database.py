import csv
import os
from .models import Caption

DATA_DIR = "data"

def save_caption_to_csv(session_id: str, caption: Caption):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    # Utilisation du pool_id pour séparer les fichiers par groupe
    file_path = os.path.join(DATA_DIR, f"session_{session_id}_pool_{caption.pool_id}.csv")
    
    file_exists = os.path.isfile(file_path)
    
    with open(file_path, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # En-tête si le fichier est nouveau
        if not file_exists:
            writer.writerow(["timestamp", "user_id", "slot_index", "text"])
        
        writer.writerow([
            caption.timestamp,
            caption.user_id,
            caption.slot_index,
            caption.text
        ])