import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os

sns.set(style="whitegrid")


def load_csv_files(folder):
    data = []
    for filename in os.listdir(folder):
        if filename.endswith(".csv"):
            filepath = os.path.join(folder, filename)
            df = pd.read_csv(filepath)
            data.append(df)
    return data


def plot_imu_comparison(data_imu, columns, output_image, imu_label="IMU"):
    plt.figure(figsize=(15, 10))
    data_concat = pd.concat([df[columns] for df in data_imu])
    for i, col in enumerate(columns, 1):
        plt.subplot(3, 2, i)
        sns.lineplot(data=data_concat[col], label=col)
        plt.title(f"{col} Trend")
        plt.xlabel("Time")
        plt.ylabel(col)
    plt.suptitle(f"{imu_label} Sensor Data Comparison", fontsize=16)
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(output_image)
    plt.close()


# Define the list of actions
actions = ["bball", "bomb", "bowling", "logout", "reload", "shield", "soccer", "volley"]
# actions = ["bomb"]

# Define the "data/" directory path relative to the automation script
base_data_folder = os.path.join(os.path.dirname(__file__), "../data")

# Define the "viz/" directory path relative to the automation script
viz_folder = os.path.join(os.path.dirname(__file__), "../viz")

# Ensure the viz folder exists
os.makedirs(viz_folder, exist_ok=True)

# List of columns to compare
columns_to_compare = ["accX", "gyrX", "accY", "gyrY", "accZ", "gyrZ"]

# Process each action
for action in actions:
    folder_imu1 = os.path.join(base_data_folder, action, "ankle")
    folder_imu2 = os.path.join(base_data_folder, action, "gun")

    # Output paths for the saved visualizations
    output_image_imu1 = os.path.join(viz_folder, f"{action}_ankle_viz.png")
    output_image_imu2 = os.path.join(viz_folder, f"{action}_gun_viz.png")

    # Load CSV files
    data_imu1 = load_csv_files(folder_imu1)
    data_imu2 = load_csv_files(folder_imu2)

    # Plot and save images for IMU 1 and IMU 2
    plot_imu_comparison(
        data_imu1,
        columns_to_compare,
        output_image_imu1,
        imu_label=f"{action.capitalize()} - IMU 1 (Ankle)",
    )
    plot_imu_comparison(
        data_imu2,
        columns_to_compare,
        output_image_imu2,
        imu_label=f"{action.capitalize()} - IMU 2 (Gun)",
    )

    print(f"Processed action: {action}")

print("All actions processed successfully.")
