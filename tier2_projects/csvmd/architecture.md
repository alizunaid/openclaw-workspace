# Architecture: csv-to-markdown-converter

## Overview
A tool that reads CSV files and converts their contents into markdown table format for better readability and integration with documentation systems.

## Subsystems

### file-reader
**Purpose:** Read the contents of a CSV file.
**Inputs:**
- Path to the CSV file
**Outputs:**
- Raw CSV data as string
**Depends on:** none
**Owns state:** stateless
**Failure modes:**
- File not found
- Permission denied

### csv-parser
**Purpose:** Parse the raw CSV string into a structured format with automatic header detection and quoting handling.
**Inputs:**
- Raw CSV data from file-reader
**Outputs:**
- List of rows, each row is a list of cells
**Depends on:** file-reader
**Owns state:** stateless
**Failure modes:**
- Malformed CSV (e.g., mismatched number of columns)

### markdown-formatter
**Purpose:** Convert the structured CSV data into markdown table format.
**Inputs:**
- List of rows from csv-parser
**Outputs:**
- Markdown formatted string
**Depends on:** csv-parser
**Owns state:** stateless
**Failure modes:**
- Empty input (no rows to convert)

### file-writer
**Purpose:** Write the markdown formatted string to a file.
**Inputs:**
- Markdown formatted string from markdown-formatter
- Destination file path
**Outputs:**
- File written confirmation
**Depends on:** markdown-formatter
**Owns state:** stateless
**Failure modes:**
- Destination directory not writable
- Permission denied

## Data flow
file-reader reads the CSV file and passes its content to csv-parser, which converts it into a structured list of rows. markdown-formatter then processes this structured data into markdown format and hands over the resulting string to file-writer for saving to a destination file.

## Cross-cutting failure modes
If the input CSV file is missing or unreadable, the conversion process halts early. Similarly, if writing to the destination file fails due to permission issues, the system will report this error without attempting further writes.

## Integration points
The data passed between subsystems follows a consistent format:
- `file-reader` outputs a single string containing the raw CSV content.
- `csv-parser` consumes this string and outputs a list of row lists.
- `markdown-formatter` accepts a list of row lists and produces a markdown formatted string.
- `file-writer` takes a markdown string and writes it to disk according to the provided path.