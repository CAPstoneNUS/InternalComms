import subprocess
import os

# Define the folder where your scripts are located
automation_folder = os.path.dirname(__file__)

# List of scripts to run in order
scripts = ["balance_entries.py", "file_mover.py", "visualize_data.py"]


# Function to print a nicely formatted header/footer for each script
def print_separator(message):
    print(f"\n{'='*20} {message} {'='*20}\n")


# Execute each script one by one
for script in scripts:
    script_path = os.path.join(automation_folder, script)

    # Print start message
    print_separator(f"Starting {script}")

    try:
        # Run the script using subprocess
        subprocess.run(["python", script_path], check=True)

        # Print success message
        print_separator(f"Successfully completed {script}")
    except subprocess.CalledProcessError as e:
        # Print failure message
        print_separator(f"Failed to complete {script}. Error: {e}")
        break  # Stop execution if any script fails
