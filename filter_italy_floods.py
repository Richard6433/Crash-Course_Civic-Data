"""
filter_italy_floods.py
======================
Reads the FloodArchive Excel file and keeps only the rows where
the Country column is "Italy". Saves the result as a new CSV file.

HOW IT WORKS (plain English)
-----------------------------
1. We open the Excel file (floodarchive.xlsx).
2. We read the first row as column names (the "header").
3. We go through every other row one by one.
4. If the Country value in that row is "Italy", we keep it.
5. After collecting all Italy rows, we write them out to a new
   CSV file called italy_floods.csv.

A CSV (Comma-Separated Values) file is a simple text file where
each line is one row of data and the values are separated by commas.
It can be opened in Excel, Google Sheets, or any text editor.
"""

import csv
import openpyxl

# ---------- Step 1: Open the Excel file ----------
# openpyxl is a library that lets Python read .xlsx files.
# read_only=True makes it faster — we only need to read, not edit.
workbook = openpyxl.load_workbook("floodarchive.xlsx", read_only=True)

# A workbook can have multiple sheets (like tabs in Excel).
# .active gives us the first/default sheet.
sheet = workbook.active

# ---------- Step 2: Read the header row ----------
# iter_rows() lets us loop through rows. values_only=True returns
# plain values instead of cell objects.
# We grab just the first row (min_row=1, max_row=1) for the column names.
rows_iterator = sheet.iter_rows(min_row=1, values_only=True)
header = next(rows_iterator)   # next() gets the very first row

# Find which column number holds the "Country" data.
# index() searches the header tuple and returns the position (0, 1, 2 ...).
country_column = header.index("Country")

# ---------- Step 3: Collect only Italy rows ----------
italy_rows = []

for row in rows_iterator:          # loop over every remaining row
    if row[country_column] == "Italy":   # check the Country value
        italy_rows.append(row)     # keep this row

print(f"Found {len(italy_rows)} flood events in Italy.")

# ---------- Step 4: Write the results to a CSV file ----------
output_file = "italy_floods.csv"

with open(output_file, "w", newline="", encoding="utf-8") as csv_file:
    # csv.writer handles all the commas and quoting automatically.
    writer = csv.writer(csv_file)

    # Write the header row first so the CSV has column names.
    writer.writerow(header)

    # Write each Italy row.
    writer.writerows(italy_rows)

print(f"Saved results to '{output_file}'.")

# ---------- Done! ----------
workbook.close()
