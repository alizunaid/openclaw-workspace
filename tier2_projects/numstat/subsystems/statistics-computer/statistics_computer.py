import sys
from statistics import mean

def compute_statistics(numbers):
    if not numbers:
        return {
            'count': 0,
            'sum': 0,
            'mean': float('nan'),
            'min': float('inf'),
            'max': float('-inf')
        }

    valid_numbers = []
    for num in numbers:
        try:
            value = float(num)
            valid_numbers.append(value)
        except ValueError:
            continue

    if not valid_numbers:
        return {
            'count': 0,
            'sum': 0,
            'mean': float('nan'),
            'min': float('inf'),
            'max': float('-inf')
        }

    count = len(valid_numbers)
    total_sum = sum(valid_numbers)
    avg_mean = mean(valid_numbers)
    minimum = min(valid_numbers)
    maximum = max(valid_numbers)

    return {
        'count': count,
        'sum': total_sum,
        'mean': avg_mean,
        'min': minimum,
        'max': maximum
    }

def main():
    if len(sys.argv) == 1:
        print("Statistics computer subsystem initialized.")
        sys.exit(0)

    # Example usage with command line arguments for testing purposes
    try:
        numbers = list(map(float, sys.argv[1:]))
        stats = compute_statistics(numbers)
        print(stats)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()