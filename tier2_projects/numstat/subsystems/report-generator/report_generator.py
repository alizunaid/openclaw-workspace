from formatter import format_summary_statistics, format_histogram_data
import sys

def generate_report(summary, histogram):
    try:
        summary_section = format_summary_statistics(summary)
        histogram_section = format_histogram_data(histogram)
        report = f"{summary_section}\n\n{histogram_section}"
        return report
    except Exception as e:
        print(f"Error generating report: {e}", file=sys.stderr)
        return None

def main():
    if len(sys.argv) == 1:
        print("Subsytem report-generator is ready.")
        sys.exit(0)

if __name__ == "__main__":
    main()