# Architecture: nexadose-rx

## Overview
A system that reads a file of numbers, computes summary statistics and builds a histogram in parallel, then writes a combined markdown report to an output file.

## Subsystems

### file-reader
**Purpose:** Read numbers from a specified input file and provide them for processing.
**Inputs:**
- path to the input file
**Outputs:**
- stream of numbers read from the file
**Depends on:** none
**Owns state:** maintains no state, reads once per invocation
**Failure modes:**
- file not found
- file unreadable

### statistics-computer
**Purpose:** Compute summary statistics (count, sum, mean, min, max) from a stream of numbers.
**Inputs:**
- stream of numbers from file-reader
**Outputs:**
- summary statistics (count, sum, mean, min, max)
**Depends on:** file-reader
**Owns state:** maintains running totals and counts during processing
**Failure modes:**
- invalid number format in input

### histogram-builder
**Purpose:** Build a histogram of counts per bucket from a stream of numbers.
**Inputs:**
- stream of numbers from file-reader
**Outputs:**
- histogram data (bucket ranges, counts per bucket)
**Depends on:** file-reader
**Owns state:** maintains bucket counts during processing
**Failure modes:**
- invalid number format in input

### report-generator
**Purpose:** Generate a markdown report combining summary statistics and histogram data.
**Inputs:**
- summary statistics from statistics-computer
- histogram data from histogram-builder
**Outputs:**
- markdown formatted report as string
**Depends on:** statistics-computer, histogram-builder
**Owns state:** constructs the final report string in memory
**Failure modes:**
- invalid input data format

### file-writer
**Purpose:** Write the generated markdown report to an output file.
**Inputs:**
- markdown formatted report from report-generator
**Outputs:**
- saved markdown file at specified path
**Depends on:** report-generator
**Owns state:** maintains no state, writes once per invocation
**Failure modes:**
- unable to write to output path

## Data flow
file-reader reads numbers from the input file and streams them in parallel to both statistics-computer and histogram-builder. statistics-computer calculates summary statistics such as count, sum, mean, min, and max. histogram-builder constructs a histogram of counts per bucket. report-generator combines the results from both processors into a markdown formatted report. finally, file-writer writes this report to the specified output file.

## Cross-cutting failure modes
If the input file is not found or unreadable, processing halts before any computations can occur. If there are errors in parsing numbers (either invalid format or value out of expected range), those numbers are skipped for statistical and histogram calculations with a warning logged.

## Integration points
Both statistics-computer and histogram-builder consume a stream of numbers from file-reader. report-generator expects both summary statistics and histogram data, formats them into a markdown string, which is then written to disk by file-writer. All subsystems communicate via passing data as first-class citizens using well-defined structures (numbers for numerical processing, dictionaries or objects for structured data like stats or histogram buckets).