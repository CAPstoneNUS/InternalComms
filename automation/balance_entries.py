import os
import pandas as pd
from datetime import timedelta


def get_csv_files_with_action_and_i(directory, action, i):
    csv_files = [
        f for f in os.listdir(directory) if f.endswith(".csv") and f"{action}_{i}" in f
    ]
    if len(csv_files) != 2:
        raise ValueError(
            f"The directory must contain exactly 2 CSV files with '{action}_{i}' in the filename."
        )
    return csv_files


def adjust_file_entries(file1, file2):
    # Load both CSV files
    df1 = pd.read_csv(file1)
    df2 = pd.read_csv(file2)

    # Convert timestamp columns to datetime
    df1["timestamp"] = pd.to_datetime(df1["timestamp"])
    df2["timestamp"] = pd.to_datetime(df2["timestamp"])

    # Get the later start time between the two files
    start_time = max(df1["timestamp"].min(), df2["timestamp"].min())

    # Filter both dataframes to start from the same timestamp
    df1 = df1[df1["timestamp"] >= start_time]
    df2 = df2[df2["timestamp"] >= start_time]

    # Get the earlier end time between the two files
    end_time = min(df1["timestamp"].max(), df2["timestamp"].max())

    # Filter both dataframes to end at the same timestamp
    df1 = df1[df1["timestamp"] <= end_time]
    df2 = df2[df2["timestamp"] <= end_time]

    # Check if any adjustments were made
    if len(df1) == len(pd.read_csv(file1)) and len(df2) == len(pd.read_csv(file2)):
        print(f"No adjustment needed for {file1} and {file2}.")
        return

    # Save the adjusted files (overwriting the original files)
    df1.to_csv(file1, index=False)
    df2.to_csv(file2, index=False)

    print(f"Adjusted {file1} and {file2}. Aligned based on timestamps.")


# List of actions
actions = [
    "bball",
    "bomb",
    "bowling",
    "logout",
    "reload",
    "shield",
    "soccer",
    "volley",
]

# Directory containing the CSV files
directory = os.path.join(os.path.dirname(__file__), "../data")

for action in actions:
    for i in range(1, 11):  # Iterate through i values (1 to 10)
        try:
            # Get the CSV files for the current action and i
            csv_files = get_csv_files_with_action_and_i(directory, action, i)

            # Adjust the file entries for the pair of CSV files
            adjust_file_entries(
                os.path.join(directory, csv_files[0]),
                os.path.join(directory, csv_files[1]),
            )
        except ValueError as e:
            # Print the error if the file pair doesn't exist or isn't valid
            print(e)
