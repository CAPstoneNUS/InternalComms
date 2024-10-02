import os
import pandas as pd


def get_csv_files_with_i(directory, i):
    # Get list of CSV files containing "_i_" in the filename
    csv_files = [
        f for f in os.listdir(directory) if f.endswith(".csv") and f"_{i}" in f
    ]

    # Ensure there are exactly 2 files for each i
    if len(csv_files) != 2:
        raise ValueError(
            f"The directory must contain exactly 2 CSV files with '_{i}' in the filename."
        )

    return csv_files


def adjust_file_entries(file1, file2):
    # Load both CSV files
    df1 = pd.read_csv(file1)
    df2 = pd.read_csv(file2)

    # Get the number of rows for each file (excluding the header)
    len1 = len(df1)
    len2 = len(df2)

    # If the number of rows is the same, no adjustment is needed
    if len1 == len2:
        print(f"No adjustment needed for {file1} and {file2}.")
        return

    # Determine the larger file and the difference in entries
    if len1 > len2:
        larger_df, smaller_df = df1, df2
        diff = len1 - len2
    else:
        larger_df, smaller_df = df2, df1
        diff = len2 - len1

    # Adjust the larger file by removing rows starting from index 1
    adjusted_df = larger_df.drop(larger_df.index[0:diff])

    # Save the adjusted file (overwriting the original file)
    output_file = file1 if len1 > len2 else file2
    adjusted_df.to_csv(output_file, index=False)

    print(f"Adjusted {output_file}. Removed {diff} entries.")


# Directory containing the CSV files
directory = os.path.join(os.path.dirname(__file__), "../data")

# Get the highest number (ending with "_i") in the CSV filenames
max_i = max(
    [
        int(f.split("_")[-1].split(".")[0])
        for f in os.listdir(directory)
        if f.endswith(".csv")
    ]
)

# Loop through the numbers 1 to 10
for i in range(1, max_i + 1):
    try:
        # Get the CSV files for the current i
        csv_files = get_csv_files_with_i(directory, i)

        # Adjust the file entries for the pair of CSV files
        adjust_file_entries(
            os.path.join(directory, csv_files[0]), os.path.join(directory, csv_files[1])
        )
    except ValueError as e:
        # Print the error if the file pair doesn't exist or isn't valid
        print(e)
