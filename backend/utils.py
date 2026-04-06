import os
import shutil

def get_unique_filename(target_dir, filename):
    base, ext = os.path.splitext(filename)
    target_path = os.path.join(target_dir, filename)
    if not os.path.exists(target_path):
        return filename
    counter = 1
    while True:
        new_filename = f"{base}_{counter}{ext}"
        new_path = os.path.join(target_dir, new_filename)
        if not os.path.exists(new_path):
            return new_filename
        counter += 1
