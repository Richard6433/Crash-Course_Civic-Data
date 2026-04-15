import pandas as pd

# --- Step 1: Read the file ---
# pandas can read Excel files (.xlsx) directly.
# We load the entire FloodArchive spreadsheet into a "DataFrame",
# which is like a table you can work with in Python.
df = pd.read_excel("floodarchive.xlsx")

print(f"Total rows in original file: {len(df)}")
print(f"Columns available: {list(df.columns)}")

# --- Step 2: Filter for France only ---
# We look through the "Country" column and keep only the rows
# where the value equals "France".
# The result is stored in a new variable called france_df.
france_df = df[df["Country"] == "France"]

print(f"Rows for France: {len(france_df)}")

# --- Step 3: Save the result as a new CSV file ---
# CSV (Comma-Separated Values) is a simple text format that any
# spreadsheet program (Excel, Google Sheets) can open.
# index=False means we don't add an extra row-number column.
france_df.to_csv("france_floods.csv", index=False)

print("Done! Results saved to france_floods.csv")
