import os
import shutil

# Define action words
actions = ["bball", "bomb", "bowling", "logout", "reload", "shield", "soccer", "volley"]

# Define the relative path to the data folder (go up one level from 'automation')
data_folder = os.path.join(os.path.dirname(__file__), "../data")

# Iterate through all files in the data folder
for filename in os.listdir(data_folder):
    # Check if the file starts with '38' or '70' and contains any action word
    if filename.startswith(("38", "70")) and filename.endswith(".csv"):
        for action in actions:
            if f"_{action}_" in filename:
                # Determine the target folder (ankle or gun)
                if filename.startswith("38"):
                    target_folder = os.path.join(data_folder, action, "gun")
                elif filename.startswith("70"):
                    target_folder = os.path.join(data_folder, action, "ankle")

                # Create the target folder if it doesn't exist
                os.makedirs(target_folder, exist_ok=True)

                # Define the source and destination file paths
                src_file = os.path.join(data_folder, filename)
                dest_file = os.path.join(target_folder, filename)

                # Move the file to the appropriate subfolder
                shutil.move(src_file, dest_file)

                # Log the file movement
                print(f"Moved {filename} to {target_folder}")
                break  # Stop checking actions once the correct one is found
