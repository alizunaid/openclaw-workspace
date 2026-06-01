import sys
from histogram_utils import calculate_histogram

def main(numbers):
    try:
        histogram = calculate_histogram(numbers)
        for bucket, count in histogram.items():
            print(f"{bucket}: {count}")
    except ValueError as e:
        print(f"Error processing input: {e}")

if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("No arguments provided; performing no-op self-check.")
        sys.exit(0)
    
    try:
        # Simulating a stream of numbers from an upstream subsystem
        numbers = [float(arg) for arg in sys.argv[1:]]
    except ValueError as e:
        print(f"Invalid input: {e}")
        sys.exit(1)
    
    main(numbers)