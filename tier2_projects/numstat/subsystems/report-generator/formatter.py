def format_summary_statistics(summary):
    lines = [
        "# Summary Statistics",
        f"- Mean: {summary.get('mean', 'N/A')}",
        f"- Median: {summary.get('median', 'N/A')}",
        f"- Mode: {summary.get('mode', 'N/A')}",
        f"- Standard Deviation: {summary.get('std_dev', 'N/A')}",
        f"- Variance: {summary.get('variance', 'N/A')}",
        f"- Min: {summary.get('min', 'N/A')}",
        f"- Max: {summary.get('max', 'N/A')}",
    ]
    return "\n".join(lines)

def format_histogram_data(histogram):
    lines = ["# Histogram Data"]
    for bin_range, count in histogram.items():
        line = f"- Range {bin_range}: {count} occurrences"
        lines.append(line)
    return "\n".join(lines)